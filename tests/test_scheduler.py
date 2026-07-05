from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from shichimimi_agent.db.migrations import migrate
from shichimimi_agent.db.repository import Repository
from shichimimi_agent.scheduler.cron import CronSchedule
from shichimimi_agent.scheduler.engine import SchedulerEngine

JST = ZoneInfo("Asia/Tokyo")


def jst(*args: int) -> datetime:
    return datetime(*args, tzinfo=JST)


class CronScheduleTest(unittest.TestCase):
    def test_exact_minute_match(self) -> None:
        cron = CronSchedule.parse("30 17 * * *")
        self.assertTrue(cron.matches(jst(2026, 7, 5, 17, 30)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 17, 31)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 18, 30)))

    def test_step_and_range(self) -> None:
        cron = CronSchedule.parse("*/30 8-23 * * *")
        self.assertTrue(cron.matches(jst(2026, 7, 5, 8, 0)))
        self.assertTrue(cron.matches(jst(2026, 7, 5, 8, 30)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 8, 15)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 7, 30)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 23, 30)) and cron.matches(jst(2026, 7, 6, 0, 0)))

    def test_list_field(self) -> None:
        cron = CronSchedule.parse("0 16 * * 1,3,5")
        # 2026-07-06 is a Monday
        self.assertTrue(cron.matches(jst(2026, 7, 6, 16, 0)))
        self.assertFalse(cron.matches(jst(2026, 7, 7, 16, 0)))

    def test_dow_zero_and_seven_are_sunday(self) -> None:
        cron0 = CronSchedule.parse("0 10 * * 0")
        cron7 = CronSchedule.parse("0 10 * * 7")
        # 2026-07-05 is a Sunday
        self.assertTrue(cron0.matches(jst(2026, 7, 5, 10, 0)))
        self.assertTrue(cron7.matches(jst(2026, 7, 5, 10, 0)))
        self.assertFalse(cron0.matches(jst(2026, 7, 6, 10, 0)))

    def test_dom_dow_or_semantics(self) -> None:
        # both restricted: matches if dom OR dow matches
        cron = CronSchedule.parse("0 0 1 * 1")
        # 2026-07-01 is a Wednesday (dom matches)
        self.assertTrue(cron.matches(jst(2026, 7, 1, 0, 0)))
        # 2026-07-06 is a Monday (dow matches, dom doesn't)
        self.assertTrue(cron.matches(jst(2026, 7, 6, 0, 0)))
        # neither matches
        self.assertFalse(cron.matches(jst(2026, 7, 2, 0, 0)))

    def test_invalid_expressions_raise(self) -> None:
        with self.assertRaises(ValueError):
            CronSchedule.parse("* * * *")
        with self.assertRaises(ValueError):
            CronSchedule.parse("60 * * * *")
        with self.assertRaises(ValueError):
            CronSchedule.parse("*/0 * * * *")
        with self.assertRaises(ValueError):
            CronSchedule.parse("abc * * * *")

    def test_next_after(self) -> None:
        cron = CronSchedule.parse("30 17 * * *")
        result = cron.next_after(jst(2026, 7, 5, 17, 30))
        self.assertEqual(result, jst(2026, 7, 6, 17, 30))

    def test_next_after_month_rollover(self) -> None:
        cron = CronSchedule.parse("0 0 1 * *")
        result = cron.next_after(jst(2026, 7, 5, 0, 0))
        self.assertEqual(result, jst(2026, 8, 1, 0, 0))

    def test_next_after_search_cap(self) -> None:
        cron = CronSchedule.parse("0 0 31 2 *")  # Feb 31 never exists
        with self.assertRaises(ValueError):
            cron.next_after(jst(2026, 7, 5, 0, 0))


@dataclass
class _FakeConfig:
    schedules: dict
    root: Path | None = None
    roles: dict | None = None
    policy: dict | None = None


def _make_config(jobs: list[dict], *, timezone: str = "Asia/Tokyo") -> _FakeConfig:
    return _FakeConfig(
        schedules={
            "defaults": {"timezone": timezone, "concurrency_policy": "forbid", "backoff_limit": 1, "enabled": True},
            "jobs": jobs,
        }
    )


class RealScheduleExpressionTest(unittest.TestCase):
    """Exercises the actual cron expressions from config/schedules.yaml."""

    def test_x_signal_collector_every_30min_8_to_23(self) -> None:
        cron = CronSchedule.parse("*/30 8-23 * * *")
        self.assertTrue(cron.matches(jst(2026, 7, 5, 8, 0)))
        self.assertTrue(cron.matches(jst(2026, 7, 5, 8, 30)))
        self.assertTrue(cron.matches(jst(2026, 7, 5, 23, 30)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 8, 15)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 7, 30)))
        self.assertFalse(cron.matches(jst(2026, 7, 6, 0, 0)))

    def test_stock_signal_fact_check_weekdays_16_00(self) -> None:
        cron = CronSchedule.parse("0 16 * * 1-5")
        # 2026-07-03 is a Friday
        self.assertTrue(cron.matches(jst(2026, 7, 3, 16, 0)))
        # weekend: 2026-07-04 Sat, 2026-07-05 Sun
        self.assertFalse(cron.matches(jst(2026, 7, 4, 16, 0)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 16, 0)))
        # rolls over to next Monday 2026-07-06
        self.assertTrue(cron.matches(jst(2026, 7, 6, 16, 0)))

    def test_daily_digest_writer_weekdays_17_30(self) -> None:
        cron = CronSchedule.parse("30 17 * * 1-5")
        self.assertTrue(cron.matches(jst(2026, 7, 3, 17, 30)))  # Friday
        self.assertFalse(cron.matches(jst(2026, 7, 4, 17, 30)))  # Saturday
        self.assertFalse(cron.matches(jst(2026, 7, 5, 17, 30)))  # Sunday
        self.assertTrue(cron.matches(jst(2026, 7, 6, 17, 30)))  # Monday

    def test_weekly_research_review_saturday_10_00(self) -> None:
        cron = CronSchedule.parse("0 10 * * 6")
        # 2026-07-04 is a Saturday
        self.assertTrue(cron.matches(jst(2026, 7, 4, 10, 0)))
        self.assertFalse(cron.matches(jst(2026, 7, 3, 10, 0)))  # Friday
        self.assertFalse(cron.matches(jst(2026, 7, 5, 10, 0)))  # Sunday

    def test_ai_it_x_daily_digest_daily_8_00(self) -> None:
        cron = CronSchedule.parse("0 8 * * *")
        for day in range(1, 8):
            self.assertTrue(cron.matches(jst(2026, 7, day, 8, 0)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 8, 1)))
        self.assertFalse(cron.matches(jst(2026, 7, 5, 7, 0)))


class SchedulerEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "app.sqlite"
        migrate(self.db_path)
        self.repository = Repository(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_run_once_dispatches_matching_job(self) -> None:
        jobs = [{"name": "job-a", "role": "x_collector", "cron": "30 17 * * *", "enabled": True}]
        config = _make_config(jobs)
        calls = []
        engine = SchedulerEngine(
            config=config,
            repository=self.repository,
            executors={"job-a": lambda job: calls.append(job)},
            now_fn=lambda: jst(2026, 7, 5, 17, 30),
        )
        results = engine.run_once()
        self.assertEqual(len(calls), 1)
        self.assertEqual(results[0].status, "succeeded")

    def test_non_matching_minute_no_dispatch(self) -> None:
        jobs = [{"name": "job-a", "role": "x_collector", "cron": "30 17 * * *", "enabled": True}]
        config = _make_config(jobs)
        calls = []
        engine = SchedulerEngine(
            config=config,
            repository=self.repository,
            executors={"job-a": lambda job: calls.append(job)},
        )
        results = engine.run_pending(jst(2026, 7, 5, 17, 31))
        self.assertEqual(calls, [])
        self.assertEqual(results, [])

    def test_no_executor_job_skipped(self) -> None:
        jobs = [{"name": "job-a", "role": "x_collector", "cron": "30 17 * * *", "enabled": True}]
        config = _make_config(jobs)
        engine = SchedulerEngine(config=config, repository=self.repository, executors={})
        results = engine.run_pending(jst(2026, 7, 5, 17, 30))
        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "no executor")

    def test_executor_retried_then_failed(self) -> None:
        jobs = [{"name": "job-a", "role": "x_collector", "cron": "30 17 * * *", "enabled": True, "backoff_limit": 2}]
        config = _make_config(jobs)
        attempts = []

        def _fail(job: dict) -> None:
            attempts.append(1)
            raise RuntimeError("boom")

        engine = SchedulerEngine(config=config, repository=self.repository, executors={"job-a": _fail})
        results = engine.run_pending(jst(2026, 7, 5, 17, 30))
        self.assertEqual(len(attempts), 3)  # backoff_limit=2 -> 1 initial + 2 retries
        self.assertEqual(results[0].status, "failed")
        self.assertIn("boom", results[0].reason)

    def test_deadline_exceeded_recorded_as_failed(self) -> None:
        jobs = [
            {
                "name": "job-a",
                "role": "x_collector",
                "cron": "30 17 * * *",
                "enabled": True,
                "backoff_limit": 0,
                "active_deadline_seconds": 0.05,
            }
        ]
        config = _make_config(jobs)

        def _slow(job: dict) -> None:
            time.sleep(1.0)

        engine = SchedulerEngine(config=config, repository=self.repository, executors={"job-a": _slow})
        results = engine.run_pending(jst(2026, 7, 5, 17, 30))
        self.assertEqual(results[0].status, "failed")
        self.assertEqual(results[0].reason, "deadline exceeded")

    def test_same_minute_double_fire_prevented(self) -> None:
        jobs = [{"name": "job-a", "role": "x_collector", "cron": "30 17 * * *", "enabled": True}]
        config = _make_config(jobs)
        calls = []
        engine = SchedulerEngine(config=config, repository=self.repository, executors={"job-a": lambda job: calls.append(job)})
        engine.run_pending(jst(2026, 7, 5, 17, 30))
        results = engine.run_pending(jst(2026, 7, 5, 17, 30))
        self.assertEqual(len(calls), 1)
        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "already fired this minute")

    def test_disabled_job_never_dispatched(self) -> None:
        jobs = [{"name": "job-a", "role": "x_collector", "cron": "30 17 * * *", "enabled": False}]
        config = _make_config(jobs)
        calls = []
        engine = SchedulerEngine(config=config, repository=self.repository, executors={"job-a": lambda job: calls.append(job)})
        results = engine.run_pending(jst(2026, 7, 5, 17, 30))
        self.assertEqual(calls, [])
        self.assertEqual(results, [])

    def test_engine_does_not_write_db_rows(self) -> None:
        # The engine no longer owns session/task lifecycle (executors do).
        jobs = [{"name": "job-a", "role": "x_collector", "cron": "30 17 * * *", "enabled": True}]
        config = _make_config(jobs)
        engine = SchedulerEngine(config=config, repository=self.repository, executors={"job-a": lambda job: None})
        engine.run_pending(jst(2026, 7, 5, 17, 30))

        with self.repository._connect() as conn:
            rows = conn.execute("SELECT status FROM tasks").fetchall()
        self.assertEqual(rows, [])

    def test_run_forever_survives_executor_exception_and_fires_next_minute(self) -> None:
        clock = [jst(2026, 7, 5, 17, 30)]
        sleep_calls = []

        def _now() -> datetime:
            return clock[0]

        def _sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            clock[0] = clock[0] + timedelta(minutes=1)
            if len(sleep_calls) >= 2:
                raise StopIteration  # break out of run_forever's infinite loop

        jobs = [{"name": "job-a", "role": "x_collector", "cron": "*/1 * * * *", "enabled": True, "backoff_limit": 0}]
        config = _make_config(jobs)
        calls = []

        def _flaky(job: dict) -> None:
            calls.append(clock[0])
            if len(calls) == 1:
                raise RuntimeError("boom")

        engine = SchedulerEngine(config=config, repository=self.repository, executors={"job-a": _flaky}, now_fn=_now, sleep_fn=_sleep)
        with self.assertRaises(StopIteration):
            engine.run_forever()

        # First minute raised, second minute still fired (loop kept going).
        self.assertEqual(len(calls), 2)

    def test_rejects_non_jst_timezone(self) -> None:
        jobs = [{"name": "job-a", "role": "x_collector", "cron": "30 17 * * *", "enabled": True}]
        config = _make_config(jobs, timezone="UTC")
        with self.assertRaises(ValueError):
            SchedulerEngine(config=config, repository=self.repository, executors={})

    def test_run_once_uses_now_fn(self) -> None:
        jobs = [{"name": "job-a", "role": "x_collector", "cron": "30 17 * * *", "enabled": True}]
        config = _make_config(jobs)
        calls = []
        engine = SchedulerEngine(
            config=config,
            repository=self.repository,
            executors={"job-a": lambda job: calls.append(job)},
            now_fn=lambda: jst(2026, 7, 5, 17, 30),
        )
        engine.run_once()
        self.assertEqual(len(calls), 1)


class ScheduleRunCliTest(unittest.TestCase):
    def test_schedule_run_once_invokes_engine(self) -> None:
        from shichimimi_agent import cli

        fake_result = [mock.Mock(job_name="job-a", status="succeeded", reason=None)]
        fake_engine = mock.Mock()
        fake_engine.run_once.return_value = fake_result

        with mock.patch.object(cli, "_load_validated_config") as load_cfg, \
             mock.patch.object(cli, "migrate"), \
             mock.patch.object(cli, "Repository") as repo_cls, \
             mock.patch.object(cli, "_build_scheduler_executors", return_value={}), \
             mock.patch("shichimimi_agent.scheduler.engine.SchedulerEngine", return_value=fake_engine):
            load_cfg.return_value = mock.Mock(root=Path("."))
            repo_cls.for_root.return_value = mock.Mock()
            args = mock.Mock(root=None, once=True)
            exit_code = cli.cmd_schedule_run(args)

        self.assertEqual(exit_code, 0)
        fake_engine.run_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
