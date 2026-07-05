"""Single-process sequential cron scheduler engine (ADR-022).

MVP scope, documented explicitly:
- Execution is sequential in the loop thread; jobs never overlap each other
  because nothing else is running while a job executes. `concurrency_policy:
  forbid` is therefore implemented as same-minute double-fire prevention
  (a job whose cron matches the same minute it last fired for is skipped),
  not as a general-purpose overlap guard.
- `active_deadline_seconds` is enforced with a helper daemon thread and
  `Thread.join(timeout)`. If the thread is still alive after the deadline,
  the run is recorded as failed ("deadline exceeded") and the engine moves
  on; the worker thread itself keeps running detached (daemonized) since
  Python offers no safe way to force-kill a thread. This is a known
  limitation of the MVP.
- `backoff_limit` is interpreted as an immediate-retry count (no backoff
  delay) before recording failure.
- The engine itself records nothing in the DB: it is only responsible for
  firing, retrying, and returning results. Each executor owns its own
  session/task lifecycle (e.g. the claude-digest executor creates its own
  session+task with real outputs per attempt). Jobs with no executor are
  reported as skipped without touching the DB.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from shichimimi_agent.util.time import now_jst

Executor = Callable[[dict[str, Any]], None]

ASSUMED_TIMEZONE = "Asia/Tokyo"


@dataclass(frozen=True)
class JobRunResult:
    job_name: str
    status: str  # "succeeded" | "failed" | "skipped"
    reason: str | None = None


@dataclass
class _JobSpec:
    name: str
    role: str
    cron_expr: str
    enabled: bool
    concurrency_policy: str
    backoff_limit: int
    active_deadline_seconds: int | None
    raw: dict[str, Any]


class SchedulerEngine:
    def __init__(
        self,
        *,
        config: Any,
        executors: dict[str, Executor],
        repository: Any = None,
        now_fn: Callable[[], datetime] = now_jst,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        # `repository` is accepted but unused by the engine itself (kept
        # for API stability / potential future engine-level bookkeeping);
        # executors are solely responsible for recording their own runs.
        self._repository = repository
        self._executors = executors
        self._now_fn = now_fn
        self._sleep_fn = sleep_fn
        self._jobs = self._load_jobs(config)
        self._last_fired: dict[str, datetime] = {}

        # local import to avoid a hard cycle at module load time
        from shichimimi_agent.scheduler.cron import CronSchedule

        self._crons: dict[str, CronSchedule] = {
            job.name: CronSchedule.parse(job.cron_expr) for job in self._jobs
        }

    @staticmethod
    def _load_jobs(config: Any) -> list[_JobSpec]:
        schedules = config.schedules or {}
        defaults = schedules.get("defaults") or {}
        timezone = defaults.get("timezone", ASSUMED_TIMEZONE)
        if timezone != ASSUMED_TIMEZONE:
            raise ValueError(
                f"unsupported schedules timezone {timezone!r}; scheduler engine assumes {ASSUMED_TIMEZONE!r} (ADR-022)"
            )

        jobs: list[_JobSpec] = []
        for raw in schedules.get("jobs") or []:
            enabled = raw.get("enabled", defaults.get("enabled", True))
            if not enabled:
                continue
            jobs.append(
                _JobSpec(
                    name=raw["name"],
                    role=raw.get("role", ""),
                    cron_expr=raw["cron"],
                    enabled=enabled,
                    concurrency_policy=raw.get("concurrency_policy", defaults.get("concurrency_policy", "forbid")),
                    backoff_limit=raw.get("backoff_limit", defaults.get("backoff_limit", 1)),
                    active_deadline_seconds=raw.get("active_deadline_seconds"),
                    raw=raw,
                )
            )
        return jobs

    def run_pending(self, at: datetime) -> list[JobRunResult]:
        results: list[JobRunResult] = []
        current_minute = at.replace(second=0, microsecond=0)

        for job in self._jobs:
            try:
                cron = self._crons[job.name]
                if not cron.matches(current_minute):
                    continue

                if job.concurrency_policy == "forbid":
                    last = self._last_fired.get(job.name)
                    if last == current_minute:
                        results.append(
                            JobRunResult(job_name=job.name, status="skipped", reason="already fired this minute")
                        )
                        continue

                self._last_fired[job.name] = current_minute
                results.append(self._dispatch(job))
            except Exception as exc:  # noqa: BLE001 - one job's bug must not break the loop
                print(f"scheduler: job {job.name!r} raised unexpectedly: {exc}", file=sys.stderr)
                results.append(JobRunResult(job_name=job.name, status="failed", reason=str(exc)))

        return results

    def _dispatch(self, job: _JobSpec) -> JobRunResult:
        executor = self._executors.get(job.name)
        if executor is None:
            print(f"scheduler: no executor registered for job {job.name!r}; skipping", file=sys.stderr)
            return JobRunResult(job_name=job.name, status="skipped", reason="no executor")

        attempts = 0
        max_attempts = max(1, job.backoff_limit + 1)
        last_error: Exception | None = None

        while attempts < max_attempts:
            attempts += 1
            try:
                if job.active_deadline_seconds:
                    self._run_with_deadline(executor, job.raw, job.active_deadline_seconds)
                else:
                    executor(job.raw)
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed silently
                last_error = exc
                print(f"scheduler: job {job.name!r} attempt {attempts} failed: {exc}", file=sys.stderr)

        if last_error is None:
            return JobRunResult(job_name=job.name, status="succeeded")

        return JobRunResult(job_name=job.name, status="failed", reason=str(last_error))

    @staticmethod
    def _run_with_deadline(executor: Executor, job: dict[str, Any], deadline_seconds: float) -> None:
        error_box: list[BaseException] = []

        def _target() -> None:
            try:
                executor(job)
            except BaseException as exc:  # noqa: BLE001 - propagated to the caller thread below
                error_box.append(exc)

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(timeout=deadline_seconds)
        if worker.is_alive():
            # Known MVP limitation: the worker thread is left running
            # daemonized; Python has no safe API to force-terminate it.
            raise RuntimeError("deadline exceeded")
        if error_box:
            raise error_box[0]

    def run_once(self) -> list[JobRunResult]:
        return self.run_pending(self._now_fn())

    def run_forever(self) -> None:
        while True:
            now = self._now_fn()
            try:
                self.run_pending(now)
            except Exception as exc:  # noqa: BLE001 - keep the resident loop alive
                print(f"scheduler: run_pending raised unexpectedly: {exc}", file=sys.stderr)
            next_minute = (now.replace(second=0, microsecond=0)) + timedelta(minutes=1)
            seconds = max(0.0, (next_minute - now).total_seconds())
            self._sleep_fn(seconds)
