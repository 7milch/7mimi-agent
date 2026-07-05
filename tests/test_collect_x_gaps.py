"""Issue #25 gap-coverage tests for `collect x`, supplementing tests/test_collect_x.py.

Covers: snippet length + no-bulk-text invariant on the full stored row, score
ordering by engagement, idempotency across two separate run_collect_x
invocations, in-batch dedup of a repeated URL within one MCP response, the
empty-result-set success path, deny-path audit success=0, CLI arg plumbing
for `collect x <query> --max-results`, and a repository-level round-trip of
record_research_queue_item -> list_research_queue (status filter, score
type, source_refs_json parsing).
"""

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


class CollectXGapsTest(unittest.TestCase):
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

    def _run(self, posts: list[dict[str, Any]], *, query: str = "ai-agent", max_results: int = 20):
        client = FakeMcpClient("http://localhost:18081", posts=posts)
        return client, run_collect_x(
            config=self.config,
            repository=self.repository,
            query=query,
            max_results=max_results,
            auth_client=self.auth_client,
            mcp_client_factory=lambda base_url: client,
        )

    # -- signal-not-evidence invariant on the *whole* stored row --------

    def test_no_bulk_text_and_snippet_bounded_across_full_row(self) -> None:
        long_text = "AI agent activity discussion thread continues " * 10  # >120 chars, well beyond original
        _, result = self._run(
            [
                {
                    "id": "1",
                    "url": "https://x.com/a/status/1",
                    "author_handle": "@a",
                    "text_redacted": long_text,
                    "like_count": 2,
                    "repost_count": 1,
                }
            ]
        )
        self.assertEqual(result.inserted_count, 1)
        items = self.repository.list_research_queue()
        self.assertEqual(len(items), 1)
        item = items[0]
        snippet = item["metadata"]["text_redacted"]
        self.assertLessEqual(len(snippet), 120)
        self.assertNotEqual(snippet, long_text)
        # No field anywhere in the row should contain the full source text.
        flat_values: list[str] = []
        for value in item.values():
            if isinstance(value, str):
                flat_values.append(value)
        for ref in item["source_refs"]:
            flat_values.extend(v for v in ref.values() if isinstance(v, str))
        for value in flat_values:
            self.assertNotIn(long_text, value)

    # -- score ordering by engagement ------------------------------------

    def test_score_reflects_engagement_ordering(self) -> None:
        _, result = self._run(
            [
                {"id": "1", "url": "https://x.com/a/status/1", "author_handle": "@a",
                 "text_redacted": "low", "like_count": 1, "repost_count": 0},
                {"id": "2", "url": "https://x.com/b/status/2", "author_handle": "@b",
                 "text_redacted": "high", "like_count": 100, "repost_count": 50},
            ]
        )
        self.assertEqual(result.inserted_count, 2)
        items = self.repository.list_research_queue()
        low = next(i for i in items if i["source_refs"][0]["url"].endswith("/1"))
        high = next(i for i in items if i["source_refs"][0]["url"].endswith("/2"))
        self.assertEqual(low["score"], 1)
        self.assertEqual(high["score"], 150)
        self.assertGreater(high["score"], low["score"])

    # -- idempotency across two separate run_collect_x calls -------------

    def test_idempotent_across_two_separate_invocations(self) -> None:
        posts = [
            {"id": "1", "url": "https://x.com/a/status/1", "author_handle": "@a",
             "text_redacted": "first", "like_count": 3, "repost_count": 1},
        ]
        client1 = FakeMcpClient("http://localhost:18081", posts=posts)
        result1 = run_collect_x(
            config=self.config,
            repository=self.repository,
            query="ai-agent",
            auth_client=self.auth_client,
            mcp_client_factory=lambda base_url: client1,
        )
        self.assertEqual(result1.inserted_count, 1)

        # Second, independent run_collect_x call (fresh factory/client instance,
        # as a second CLI invocation would be) against the same repository/db.
        client2 = FakeMcpClient("http://localhost:18081", posts=posts)
        result2 = run_collect_x(
            config=self.config,
            repository=self.repository,
            query="ai-agent",
            auth_client=self.auth_client,
            mcp_client_factory=lambda base_url: client2,
        )
        self.assertEqual(result2.inserted_count, 0)
        self.assertEqual(len(result2.item_ids), 0)
        self.assertEqual(len(self.repository.list_research_queue()), 1)

    # -- dedup within a single result set (same URL twice) ----------------

    def test_dedup_when_same_url_appears_twice_in_one_batch(self) -> None:
        posts = [
            {"id": "1", "url": "https://x.com/a/status/1", "author_handle": "@a",
             "text_redacted": "dup1", "like_count": 1, "repost_count": 0},
            {"id": "1-retweet-view", "url": "https://x.com/a/status/1", "author_handle": "@a",
             "text_redacted": "dup2", "like_count": 9, "repost_count": 9},
        ]
        _, result = self._run(posts)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(len(self.repository.list_research_queue()), 1)

    # -- empty result set --------------------------------------------------

    def test_empty_result_set_succeeds_with_zero_rows(self) -> None:
        _, result = self._run([])
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.item_ids, [])
        self.assertEqual(self.repository.list_research_queue(), [])

    # -- deny path audits success=0 ----------------------------------------

    def test_deny_path_audits_success_zero(self) -> None:
        policy = dict(self.config.policy)
        role_policy = dict(policy["role_tool_policy"])
        role_policy["x_collector"] = {"allow": [], "deny": ["x.*"]}
        policy["role_tool_policy"] = role_policy
        denying_engine = PolicyEngine(policy)
        denying_auth_client = AuthProxyClient(local_fallback_engine=denying_engine)

        client = FakeMcpClient("http://localhost:18081", posts=[
            {"id": "1", "url": "https://x.com/a/status/1", "author_handle": "@a",
             "text_redacted": "x", "like_count": 1, "repost_count": 0},
        ])
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

        conn = self.repository._connect()
        try:
            rows = conn.execute(
                "SELECT tool_name, success FROM tool_events ORDER BY rowid DESC LIMIT 1"
            ).fetchall()
            session_status = conn.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
            task_status = conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        tool_name, success = rows[0]
        self.assertEqual(tool_name, "x.search_posts_recent")
        self.assertEqual(success, 0)
        # No research_queue row should have been recorded on the deny path.
        self.assertEqual(self.repository.list_research_queue(), [])
        # The session/task must be left in a terminal "failed" state, not
        # stuck "running"/"queued" (matches the codebase's failure-handling).
        self.assertEqual(session_status, "failed")
        self.assertEqual(task_status, "failed")


class RepositoryResearchQueueRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "app.sqlite"
        migrate(db_path)
        self.repository = Repository(db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_round_trip_preserves_fields_and_status_filter(self) -> None:
        item_id = self.repository.record_research_queue_item(
            source="x",
            topic="ai-agent",
            reason="X signal (engagement 42)",
            source_refs=[{"type": "url", "url": "https://x.com/a/status/1", "author": "@a"}],
            score=42,
            status="new",
            assigned_role="x_collector",
            metadata={"post_id": "1", "text_redacted": "hello"},
        )
        other_id = self.repository.record_research_queue_item(
            source="x",
            topic="other-topic",
            reason="X signal (engagement 1)",
            source_refs=[{"type": "url", "url": "https://x.com/b/status/2", "author": "@b"}],
            score=1,
            status="done",
            assigned_role="x_collector",
        )

        all_items = self.repository.list_research_queue()
        self.assertEqual({i["id"] for i in all_items}, {item_id, other_id})

        item = next(i for i in all_items if i["id"] == item_id)
        self.assertEqual(item["source"], "x")
        self.assertEqual(item["topic"], "ai-agent")
        self.assertEqual(item["reason"], "X signal (engagement 42)")
        self.assertIsInstance(item["score"], int)
        self.assertEqual(item["score"], 42)
        self.assertEqual(item["status"], "new")
        self.assertEqual(item["assigned_role"], "x_collector")
        self.assertIsInstance(item["source_refs"], list)
        self.assertEqual(item["source_refs"][0]["url"], "https://x.com/a/status/1")
        self.assertEqual(item["source_refs"][0]["author"], "@a")
        self.assertEqual(item["metadata"]["post_id"], "1")

        new_only = self.repository.list_research_queue(status="new")
        self.assertEqual([i["id"] for i in new_only], [item_id])

        done_only = self.repository.list_research_queue(status="done")
        self.assertEqual([i["id"] for i in done_only], [other_id])

        none_status = self.repository.list_research_queue(status="does-not-exist")
        self.assertEqual(none_status, [])


class CollectXCliArgTest(unittest.TestCase):
    def test_collect_x_query_and_max_results_parsed(self) -> None:
        from shichimimi_agent.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["collect", "x", "AI agent", "--max-results", "7"])
        self.assertEqual(args.collect_command, "x")
        self.assertEqual(args.query, "AI agent")
        self.assertEqual(args.max_results, 7)
        self.assertIs(args.func.__name__, "cmd_collect_x")

    def test_collect_x_default_max_results(self) -> None:
        from shichimimi_agent.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["collect", "x", "AI agent"])
        self.assertEqual(args.max_results, 20)

    def test_collect_x_requires_query(self) -> None:
        from shichimimi_agent.cli import build_parser

        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["collect", "x"])

    def test_collect_missing_subcommand_errors(self) -> None:
        from shichimimi_agent.cli import build_parser

        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["collect"])

    def test_collect_x_dispatch_invokes_run_collect_x_with_parsed_args(self) -> None:
        from unittest import mock

        from shichimimi_agent import cli as cli_mod

        args = cli_mod.build_parser().parse_args(["collect", "x", "ai-agent", "--max-results", "5"])

        with mock.patch.object(cli_mod, "_load_validated_config") as load_cfg, \
             mock.patch("shichimimi_agent.db.migrate") as migrate_mock, \
             mock.patch.object(cli_mod.Repository, "for_root") as for_root_mock, \
             mock.patch("shichimimi_agent.runner.collect_x.run_collect_x") as run_mock:
            load_cfg.return_value = mock.Mock(root=Path("."))
            for_root_mock.return_value = mock.Mock()
            result_obj = mock.Mock(status="succeeded", query="ai-agent", inserted_count=0, item_ids=[])
            run_mock.return_value = result_obj

            exit_code = args.func(args)

        self.assertEqual(exit_code, 0)
        run_mock.assert_called_once()
        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs["query"], "ai-agent")
        self.assertEqual(kwargs["max_results"], 5)


if __name__ == "__main__":
    unittest.main()
