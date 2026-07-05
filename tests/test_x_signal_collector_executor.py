from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shichimimi_agent import cli
from shichimimi_agent.config.loader import AppConfig
from shichimimi_agent.db.migrations import migrate
from shichimimi_agent.db.repository import Repository
from shichimimi_agent.scheduler.engine import SchedulerEngine


def _make_config(root: Path) -> AppConfig:
    return AppConfig(
        root=root,
        roles={"roles": {"x_collector": {}}},
        policy={},
        schedules={
            "version": 1,
            "defaults": {"timezone": "Asia/Tokyo", "concurrency_policy": "forbid", "backoff_limit": 0, "enabled": True},
            "jobs": [
                {
                    "name": "x-signal-collector",
                    "role": "x_collector",
                    "enabled": True,
                    "cron": "*/30 8-23 * * *",
                    "inputs": {"query_set": "default_x_watch", "max_posts_per_query": 999},
                }
            ],
            "query_sets": {
                "default_x_watch": {
                    "queries": ["query one", "query two"],
                }
            },
        },
    )


class XSignalCollectorExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.db_path = self.root / "app.sqlite"
        migrate(self.db_path)
        self.repository = Repository(self.db_path)
        self.config = _make_config(self.root)

    def _clear_env(self) -> None:
        for name in ("X_MCP_URL", "X_MCP_SESSION_TOKEN"):
            os.environ.pop(name, None)

    def test_calls_run_collect_x_once_per_query_with_capped_max_results(self) -> None:
        self._clear_env()
        os.environ["X_MCP_URL"] = "http://example.invalid"
        os.environ["X_MCP_SESSION_TOKEN"] = "token"
        try:
            calls: list[dict] = []

            def fake_run_collect_x(*, config, repository, query, max_results, **kwargs):
                calls.append({"query": query, "max_results": max_results})

            with mock.patch("shichimimi_agent.runner.collect_x.run_collect_x", fake_run_collect_x):
                executors = cli._build_scheduler_executors(self.config, self.repository)
                job = self.config.schedules["jobs"][0]
                executors["x-signal-collector"](job)

            self.assertEqual(len(calls), 2)
            self.assertEqual([c["query"] for c in calls], ["query one", "query two"])
            # max_posts_per_query=999 in job inputs must be capped at 50
            for c in calls:
                self.assertEqual(c["max_results"], 50)
        finally:
            self._clear_env()

    def test_missing_env_raises_runtime_error(self) -> None:
        self._clear_env()
        executors = cli._build_scheduler_executors(self.config, self.repository)
        job = self.config.schedules["jobs"][0]
        with self.assertRaises(RuntimeError):
            executors["x-signal-collector"](job)

    def test_engine_dispatches_x_signal_collector(self) -> None:
        self._clear_env()
        os.environ["X_MCP_URL"] = "http://example.invalid"
        os.environ["X_MCP_SESSION_TOKEN"] = "token"
        try:
            calls: list[str] = []

            def fake_run_collect_x(*, config, repository, query, max_results, **kwargs):
                calls.append(query)

            with mock.patch("shichimimi_agent.runner.collect_x.run_collect_x", fake_run_collect_x):
                executors = cli._build_scheduler_executors(self.config, self.repository)
                engine = SchedulerEngine(config=self.config, executors=executors, repository=self.repository)
                from datetime import datetime
                from zoneinfo import ZoneInfo

                at = datetime(2026, 7, 6, 8, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
                results = engine.run_pending(at)

            statuses = {r.job_name: r.status for r in results}
            self.assertEqual(statuses.get("x-signal-collector"), "succeeded")
            self.assertEqual(calls, ["query one", "query two"])
        finally:
            self._clear_env()


if __name__ == "__main__":
    unittest.main()
