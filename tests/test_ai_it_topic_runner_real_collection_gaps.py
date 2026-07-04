from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from shichimimi_agent.config import load_config
from shichimimi_agent.db import Repository, migrate
from shichimimi_agent.roles.ai_it_topic_runner import AiItTopicRunner
from shichimimi_agent.security.policy_engine import PolicyEngine


class FakeMcpClient:
    """In-process stand-in for McpHttpClient, keyed by query -> posts payload."""

    def __init__(self, base_url: str, *, posts_by_query: dict[str, list[dict[str, Any]]] | None = None, error: str | None = None) -> None:
        self.base_url = base_url
        self.posts_by_query = posts_by_query or {}
        self.error = error
        self.initialized = False
        self.initialize_count = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def initialize(self) -> dict[str, Any]:
        self.initialized = True
        self.initialize_count += 1
        return {"protocolVersion": "2025-03-26"}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if self.error is not None:
            return {"content": [{"type": "text", "text": self.error}], "isError": True}
        query = arguments["query"]
        posts = self.posts_by_query.get(query, [])
        text = json.dumps({"posts": posts})
        return {"content": [{"type": "text", "text": text}], "isError": False}


def _post(
    post_id: str,
    url: str,
    text: str,
    urls: list[str],
    likes: int | None = 0,
    reposts: int | None = 0,
    *,
    with_engagement: bool = True,
) -> dict[str, Any]:
    post: dict[str, Any] = {
        "id": post_id,
        "url": url,
        "author_handle": "alice",
        "created_at": "2026-07-01T00:00:00Z",
        "text_redacted": text,
        "urls": urls,
        "topics": [],
        "collected_at": "2026-07-01T00:05:00Z",
    }
    if with_engagement:
        post["engagement"] = {"like_count": likes, "repost_count": reposts}
    return post


class AiItTopicRunnerRealCollectionGapsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.config = load_config(self.root)
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "app.sqlite"
        migrate(db_path)
        self.repository = Repository(db_path)
        self.policy_engine = PolicyEngine(self.config.policy)
        self._env_backup = dict(os.environ)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        os.environ.clear()
        os.environ.update(self._env_backup)

    def _job(self) -> dict[str, Any]:
        return {
            "role": "ai_it_topic_runner",
            "inputs": {"query_set": "ai_it_watch"},
            "output": {"repo": "nishiog/ai-it-research-notes"},
        }

    def _queries(self) -> list[str]:
        query_set = (self.config.schedules.get("query_sets") or {}).get("ai_it_watch") or {}
        return list(query_set.get("queries") or [])

    def _runner(self, fake_client: FakeMcpClient) -> AiItTopicRunner:
        os.environ["X_MCP_URL"] = "http://x-mcp.local"
        return AiItTopicRunner(
            config=self.config,
            repository=self.repository,
            policy_engine=self.policy_engine,
            mcp_client_factory=lambda base_url: fake_client,
        )

    def test_engagement_tie_break_first_post_wins(self) -> None:
        queries = self._queries()
        self.assertTrue(queries)
        query = queries[0]
        posts = [
            _post("tie-first", "https://x.com/alice/status/tiefirst", "first tied post", [], likes=5, reposts=5),
            _post("tie-second", "https://x.com/alice/status/tiesecond", "second tied post", [], likes=10, reposts=0),
        ]
        fake_client = FakeMcpClient("http://x-mcp.local", posts_by_query={query: posts})
        runner = self._runner(fake_client)

        result = runner.run_daily_digest(session_id="s1", task_id="t1", job=self._job(), dry_run=True)
        markdown_path = Path(result.path)
        content = markdown_path.read_text(encoding="utf-8")
        # Strictly-greater comparison in implementation means the first post
        # (score 10) should be chosen over the second (score 10) since `>` not `>=`.
        self.assertIn("first tied post", content)
        self.assertNotIn("second tied post", content)

    def test_missing_engagement_field_treated_as_zero_no_crash(self) -> None:
        queries = self._queries()
        query = queries[0]
        posts = [
            _post("no-engagement", "https://x.com/alice/status/noeng", "post without engagement field", [], with_engagement=False),
            _post("with-engagement", "https://x.com/alice/status/witheng", "post with some engagement", [], likes=1, reposts=0),
        ]
        fake_client = FakeMcpClient("http://x-mcp.local", posts_by_query={query: posts})
        runner = self._runner(fake_client)

        result = runner.run_daily_digest(session_id="s2", task_id="t2", job=self._job(), dry_run=True)
        self.assertEqual(result.status, "succeeded")
        content = Path(result.path).read_text(encoding="utf-8")
        # Post with engagement (score 1) beats missing-engagement post (score 0)
        self.assertIn("post with some engagement", content)

    def test_what_happened_truncated_to_200_chars_and_whitespace_collapsed(self) -> None:
        queries = self._queries()
        query = queries[0]
        raw_text = ("word " * 60) + "\n\n  tail  text  "
        posts = [_post("long", "https://x.com/alice/status/long", raw_text, [], likes=1, reposts=0)]
        fake_client = FakeMcpClient("http://x-mcp.local", posts_by_query={query: posts})
        runner = self._runner(fake_client)

        result = runner.run_daily_digest(session_id="s3", task_id="t3", job=self._job(), dry_run=True)
        source_refs = result.source_refs
        matched = [ref for ref in source_refs if ref["topic"] == query]
        self.assertEqual(len(matched), 1)

        collapsed = " ".join(raw_text.split())
        expected_prefix = collapsed[:200]
        self.assertLessEqual(len(expected_prefix), 200)
        content = Path(result.path).read_text(encoding="utf-8")
        self.assertIn(expected_prefix + " (via X signal)", content)
        # No raw double-space / newline sequences should survive from the source text
        self.assertNotIn("  tail  text", content)

    def test_evidence_url_empty_when_no_expanded_urls(self) -> None:
        # X posts are signals, never evidence: when a post has no expanded
        # external URL, evidence_url must be empty (never the X post URL
        # itself). The X post URL is only ever exposed via x_signal_url.
        queries = self._queries()
        query = queries[0]
        posts = [_post("nourls", "https://x.com/alice/status/nourls", "post with no urls field", [], likes=1, reposts=0)]
        fake_client = FakeMcpClient("http://x-mcp.local", posts_by_query={query: posts})
        runner = self._runner(fake_client)

        result = runner.run_daily_digest(session_id="s4", task_id="t4", job=self._job(), dry_run=True)
        matched = [ref for ref in result.source_refs if ref["topic"] == query]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["url"], "")

        content = Path(result.path).read_text(encoding="utf-8")
        self.assertIn("(未確認 — 要ファクトチェック)", content)
        self.assertIn("https://x.com/alice/status/nourls", content)  # still present as x_signal_url

    def test_partial_results_one_query_zero_posts_digest_still_produced(self) -> None:
        queries = self._queries()
        self.assertGreaterEqual(len(queries), 2)
        posts_by_query = {
            queries[0]: [_post("p1", "https://x.com/alice/status/p1", "only query with posts", [], likes=3, reposts=1)],
            # queries[1] (and beyond) intentionally return zero posts.
        }
        fake_client = FakeMcpClient("http://x-mcp.local", posts_by_query=posts_by_query)
        runner = self._runner(fake_client)

        result = runner.run_daily_digest(session_id="s5", task_id="t5", job=self._job(), dry_run=True)
        self.assertEqual(result.status, "succeeded")
        topics = {ref["topic"] for ref in result.source_refs}
        self.assertEqual(topics, {queries[0]})
        # The MCP client should still have been called once per query (up to 3).
        self.assertEqual(len(fake_client.calls), len(queries[:3]))

    def test_run_daily_digest_end_to_end_writes_digest_and_records_document_and_audit(self) -> None:
        queries = self._queries()
        posts_by_query = {
            query: [_post(f"p{i}", f"https://x.com/alice/status/p{i}", f"post for {query}", [], likes=2, reposts=1)]
            for i, query in enumerate(queries[:3])
        }
        fake_client = FakeMcpClient("http://x-mcp.local", posts_by_query=posts_by_query)
        runner = self._runner(fake_client)

        # tool_events has FK constraints on sessions/tasks; create real rows the
        # way the CLI entrypoint does, instead of passing arbitrary ids.
        session_id = self.repository.create_session(source="test", role="ai_it_topic_runner", workspace_path="/tmp/ws")
        task_id = self.repository.create_task(session_id=session_id, role="ai_it_topic_runner", input_data=self._job())

        result = runner.run_daily_digest(session_id=session_id, task_id=task_id, job=self._job(), dry_run=True)
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(Path(result.path).exists())

        conn = self.repository._connect()
        try:
            doc_rows = conn.execute("SELECT doc_type, status FROM documents").fetchall()
            self.assertEqual(len(doc_rows), 1)
            self.assertEqual(doc_rows[0][0], "ai_it_daily_digest")
            self.assertEqual(doc_rows[0][1], "draft")

            event_rows = conn.execute(
                "SELECT tool_name, success FROM tool_events WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            # One search event per query plus one commit_and_push_markdown_repo event.
            self.assertEqual(len(event_rows), len(queries[:3]) + 1)
            tool_names = {row[0] for row in event_rows}
            self.assertIn("x.search_posts_recent", tool_names)
            self.assertIn("document.commit_and_push_markdown_repo", tool_names)
        finally:
            conn.close()

    def test_mcp_initialize_called_once_per_run_not_per_query(self) -> None:
        queries = self._queries()
        self.assertGreaterEqual(len(queries), 2)
        posts_by_query = {
            query: [_post(f"q{i}", f"https://x.com/alice/status/q{i}", f"post {i}", [], likes=1, reposts=0)]
            for i, query in enumerate(queries[:3])
        }
        fake_client = FakeMcpClient("http://x-mcp.local", posts_by_query=posts_by_query)
        runner = self._runner(fake_client)

        runner.run_daily_digest(session_id="s7", task_id="t7", job=self._job(), dry_run=True)
        self.assertEqual(fake_client.initialize_count, 1)


if __name__ == "__main__":
    unittest.main()
