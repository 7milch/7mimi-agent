from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from shichimimi_agent.config import load_config
from shichimimi_agent.db import Repository, migrate
from shichimimi_agent.proxies.auth_proxy_client import AuthProxyClient
from shichimimi_agent.runner.collect_x import run_collect_x
from shichimimi_agent.security.policy_engine import PolicyEngine


class FakeMcpClient:
    def __init__(self, base_url: str, *, posts: list[dict[str, Any]] | None = None, error: str | None = None) -> None:
        self.base_url = base_url
        self.posts = posts if posts is not None else []
        self.error = error
        self.initialized = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def initialize(self) -> dict[str, Any]:
        self.initialized = True
        return {"protocolVersion": "2025-03-26"}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if name != "x.search_posts_recent":
            raise AssertionError(f"unexpected tool call: {name}")
        if self.error is not None:
            return {"content": [{"type": "text", "text": self.error}], "isError": True}
        return {"content": [{"type": "text", "text": json.dumps({"posts": self.posts})}], "isError": False}


class CollectXTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.config = load_config(self.root)
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "app.sqlite"
        migrate(db_path)
        self.repository = Repository(db_path)
        self.policy_engine = PolicyEngine(self.config.policy)
        self.auth_client = AuthProxyClient(local_fallback_engine=self.policy_engine)
        self._env_backup = dict(os.environ)
        os.environ["X_MCP_URL"] = "http://localhost:18081"
        os.environ["X_MCP_SESSION_TOKEN"] = "test-token"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        os.environ.clear()
        os.environ.update(self._env_backup)

    def _posts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "1",
                "url": "https://x.com/a/status/1",
                "author_handle": "@a",
                "text_redacted": "AI agent 話題になってる" * 5,
                "like_count": 10,
                "repost_count": 5,
            },
            {
                "id": "2",
                "url": "https://x.com/b/status/2",
                "author_handle": "@b",
                "text_redacted": "another post",
                "like_count": 1,
                "repost_count": 0,
            },
        ]

    def test_happy_path_inserts_research_queue_rows(self) -> None:
        client = FakeMcpClient("http://localhost:18081", posts=self._posts())
        result = run_collect_x(
            config=self.config,
            repository=self.repository,
            query="ai-agent",
            max_results=20,
            auth_client=self.auth_client,
            mcp_client_factory=lambda base_url: client,
        )
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.inserted_count, 2)
        self.assertEqual(len(result.item_ids), 2)

        items = self.repository.list_research_queue()
        self.assertEqual(len(items), 2)
        first = next(i for i in items if i["source_refs"][0]["url"] == "https://x.com/a/status/1")
        self.assertEqual(first["source"], "x")
        self.assertEqual(first["topic"], "ai-agent")
        self.assertEqual(first["score"], 15)
        self.assertEqual(first["status"], "new")
        self.assertEqual(first["assigned_role"], "x_collector")
        self.assertEqual(first["source_refs"][0]["author"], "@a")
        # No full post text is stored -- only a short redacted snippet.
        self.assertLessEqual(len(first["metadata"]["text_redacted"]), 120)
        for item in items:
            for value in item.values():
                if isinstance(value, str):
                    self.assertNotIn("AI agent 話題になってる" * 5, value)

    def test_max_results_plumbed_to_tool_call(self) -> None:
        client = FakeMcpClient("http://localhost:18081", posts=[])
        run_collect_x(
            config=self.config,
            repository=self.repository,
            query="ai-agent",
            max_results=7,
            auth_client=self.auth_client,
            mcp_client_factory=lambda base_url: client,
        )
        self.assertEqual(client.calls[0], ("x.search_posts_recent", {"query": "ai-agent", "max_results": 7}))

    def test_idempotent_rerun_inserts_no_duplicates(self) -> None:
        client = FakeMcpClient("http://localhost:18081", posts=self._posts())
        run_collect_x(
            config=self.config,
            repository=self.repository,
            query="ai-agent",
            auth_client=self.auth_client,
            mcp_client_factory=lambda base_url: client,
        )
        second = run_collect_x(
            config=self.config,
            repository=self.repository,
            query="ai-agent",
            auth_client=self.auth_client,
            mcp_client_factory=lambda base_url: client,
        )
        self.assertEqual(second.inserted_count, 0)
        self.assertEqual(len(self.repository.list_research_queue()), 2)

    def _session_and_task_status(self, session_id: str, task_id: str) -> tuple[str, str]:
        conn = self.repository._connect()
        try:
            session_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
            task_status = conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        return session_status, task_status

    def test_deny_path_raises_permission_error(self) -> None:
        policy = dict(self.config.policy)
        role_policy = dict(policy["role_tool_policy"])
        role_policy["x_collector"] = {"allow": [], "deny": ["x.*"]}
        policy["role_tool_policy"] = role_policy
        denying_engine = PolicyEngine(policy)
        denying_auth_client = AuthProxyClient(local_fallback_engine=denying_engine)

        client = FakeMcpClient("http://localhost:18081", posts=self._posts())
        session_id = self.repository.create_session(source="test", role="x_collector", workspace_path="")
        task_id = self.repository.create_task(session_id=session_id, role="x_collector", input_data={"query": "ai-agent"})
        with self.assertRaises(PermissionError):
            run_collect_x(
                config=self.config,
                repository=self.repository,
                query="ai-agent",
                session_id=session_id,
                task_id=task_id,
                auth_client=denying_auth_client,
                mcp_client_factory=lambda base_url: client,
            )

        session_status, task_status = self._session_and_task_status(session_id, task_id)
        self.assertEqual(session_status, "failed")
        self.assertEqual(task_status, "failed")

    def test_mcp_error_raises_runtime_error_status_only(self) -> None:
        client = FakeMcpClient("http://localhost:18081", error="X API error (status=429)")
        session_id = self.repository.create_session(source="test", role="x_collector", workspace_path="")
        task_id = self.repository.create_task(session_id=session_id, role="x_collector", input_data={"query": "ai-agent"})
        with self.assertRaises(RuntimeError) as ctx:
            run_collect_x(
                config=self.config,
                repository=self.repository,
                query="ai-agent",
                session_id=session_id,
                task_id=task_id,
                auth_client=self.auth_client,
                mcp_client_factory=lambda base_url: client,
            )
        self.assertIn("status=429", str(ctx.exception))

        session_status, task_status = self._session_and_task_status(session_id, task_id)
        self.assertEqual(session_status, "failed")
        self.assertEqual(task_status, "failed")


class RepositoryResearchQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "app.sqlite"
        migrate(db_path)
        self.repository = Repository(db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_record_and_list_research_queue_item(self) -> None:
        item_id = self.repository.record_research_queue_item(
            source="x",
            topic="ai-agent",
            reason="X signal (engagement 3)",
            source_refs=[{"type": "url", "url": "https://x.com/a/status/1", "author": "@a"}],
            score=3,
            assigned_role="x_collector",
            metadata={"post_id": "1"},
        )
        self.assertTrue(item_id)
        items = self.repository.list_research_queue()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], item_id)
        self.assertEqual(items[0]["status"], "new")

        new_items = self.repository.list_research_queue(status="new")
        self.assertEqual(len(new_items), 1)
        other_items = self.repository.list_research_queue(status="done")
        self.assertEqual(len(other_items), 0)


if __name__ == "__main__":
    unittest.main()
