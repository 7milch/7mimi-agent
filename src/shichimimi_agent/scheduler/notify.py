"""ADR-034: scheduler job-result -> Slack syslog channel notification.

SchedulerEngine (ADR-022) fires a `notifier: Callable[[JobRunResult], None]`
exactly once per terminal (succeeded/failed) result, injected the same way as
executors/now_fn/sleep_fn. The engine itself does not know about Slack, auth-
proxy, or fail-open semantics -- all of that lives here:

- `format_job_run_notification` is a pure function of JobRunResult (TL rev.2
  point 1), trivially unit-testable without any network/client involved.
- `build_syslog_notifier` wires a `SlackNotifyClient(target="syslog")` call
  and wraps it so the notifier callable itself never raises: any failure
  (auth-proxy unreachable, SLACK_SYSLOG_CHANNEL_ID unconfigured -> 400, etc.)
  is logged to stderr and swallowed here (fail-open, the same policy as
  post_tool_use audit logging), because SchedulerEngine deliberately does
  *not* catch notifier exceptions itself (TL rev.2 point 2) -- fail-open is
  the injected callable's responsibility, not the engine's.
"""

from __future__ import annotations

import sys
from typing import Callable

from shichimimi_agent.proxies.slack_notify_client import SlackNotifyClient, SlackNotifyError
from shichimimi_agent.runner.claude_digest import error_excerpt
from shichimimi_agent.scheduler.engine import JobRunResult

_STATUS_LABELS = {"succeeded": "OK", "failed": "NG"}


def format_job_run_notification(result: JobRunResult) -> str:
    """Render a JobRunResult into syslog-channel notification text.

    Only meaningful for status in {"succeeded", "failed"} -- SchedulerEngine
    never notifies for "skipped" -- but this function does not itself
    enforce that; it just renders whatever status it's given.
    """
    status_label = _STATUS_LABELS.get(result.status, result.status)
    duration = f"{result.duration_seconds:.1f}s" if result.duration_seconds is not None else "-"
    attempts = str(result.attempts) if result.attempts is not None else "-"

    lines = [f"[scheduler] job={result.job_name} status={status_label} duration={duration} attempts={attempts}"]

    if result.status == "failed":
        excerpt = error_excerpt(result.reason)
        if excerpt:
            lines.append(f"error: {excerpt}")

    return "\n".join(lines)


def build_syslog_notifier(base_url: str, session_token: str) -> Callable[[JobRunResult], None]:
    """Build a fail-open notifier callable suitable for SchedulerEngine's
    `notifier` constructor param, backed by auth-proxy's /v1/slack/notify
    with target="syslog" (ADR-034).

    All exceptions -- SlackNotifyError (auth-proxy denied/unreachable/
    upstream Slack error) as well as anything else unexpected -- are caught
    and logged to stderr only; the caller (SchedulerEngine) never sees them,
    so a broken/misconfigured syslog channel can never affect job success.
    """
    client = SlackNotifyClient(base_url=base_url, session_token=session_token)

    def _notify(result: JobRunResult) -> None:
        try:
            text = format_job_run_notification(result)
            client.notify(text, target="syslog")
        except SlackNotifyError as exc:
            print(f"scheduler-notify: syslog notification failed for job {result.job_name!r}: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - fail-open: never let notification break the scheduler
            print(
                f"scheduler-notify: unexpected error sending syslog notification for job {result.job_name!r}: {exc}",
                file=sys.stderr,
            )

    return _notify
