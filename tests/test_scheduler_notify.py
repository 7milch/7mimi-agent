"""ADR-034: scheduler.notify -- pure-function text formatting plus the
fail-open Slack syslog notifier wrapper (Issue #34)."""

from __future__ import annotations

import http.server
import json
import threading
import unittest
from unittest import mock

from shichimimi_agent.scheduler.engine import JobRunResult
from shichimimi_agent.scheduler.notify import build_syslog_notifier, format_job_run_notification


class FormatJobRunNotificationTest(unittest.TestCase):
    def test_succeeded_includes_job_status_duration_attempts(self) -> None:
        result = JobRunResult(job_name="ai-it-x-daily-digest", status="succeeded", duration_seconds=12.34, attempts=1)
        text = format_job_run_notification(result)
        self.assertIn("job=ai-it-x-daily-digest", text)
        self.assertIn("status=OK", text)
        self.assertIn("duration=12.3s", text)
        self.assertIn("attempts=1", text)
        self.assertNotIn("error:", text)

    def test_failed_includes_status_ng_and_error_excerpt(self) -> None:
        result = JobRunResult(job_name="job-a", status="failed", reason="boom traceback", duration_seconds=1.0, attempts=2)
        text = format_job_run_notification(result)
        self.assertIn("status=NG", text)
        self.assertIn("attempts=2", text)
        self.assertIn("error: boom traceback", text)

    def test_failed_with_no_reason_has_no_error_line(self) -> None:
        result = JobRunResult(job_name="job-a", status="failed", reason=None, duration_seconds=1.0, attempts=1)
        text = format_job_run_notification(result)
        self.assertNotIn("error:", text)

    def test_failed_reason_is_bounded_via_error_excerpt(self) -> None:
        # runner.claude_digest.error_excerpt truncates at 4000 chars +
        # "... (truncated)" -- reused here rather than reimplemented, per
        # the issue spec.
        long_reason = "x" * 10000
        result = JobRunResult(job_name="job-a", status="failed", reason=long_reason, duration_seconds=1.0, attempts=1)
        text = format_job_run_notification(result)
        self.assertIn("... (truncated)", text)
        self.assertLess(len(text), len(long_reason))

    def test_missing_duration_and_attempts_render_as_dash(self) -> None:
        result = JobRunResult(job_name="job-a", status="succeeded")
        text = format_job_run_notification(result)
        self.assertIn("duration=-", text)
        self.assertIn("attempts=-", text)


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _RecordingAuthProxyHandler(http.server.BaseHTTPRequestHandler):
    """Stands in for auth-proxy's /v1/slack/notify: records the decoded JSON
    body of every POST and always replies {"chunks": 1}."""

    received: list[dict] = []

    def log_message(self, *args: object) -> None:  # silence default stderr logging
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8"))
        self.__class__.received.append(payload)
        resp = json.dumps({"chunks": 1}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


class BuildSyslogNotifierTest(unittest.TestCase):
    def setUp(self) -> None:
        _RecordingAuthProxyHandler.received = []
        self.port = _free_port()
        self.server = http.server.HTTPServer(("127.0.0.1", self.port), _RecordingAuthProxyHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def test_success_posts_target_syslog_with_formatted_text(self) -> None:
        notifier = build_syslog_notifier(f"http://127.0.0.1:{self.port}", "test-token")
        result = JobRunResult(job_name="job-a", status="succeeded", duration_seconds=1.0, attempts=1)

        notifier(result)  # must not raise

        self.assertEqual(len(_RecordingAuthProxyHandler.received), 1)
        self.assertEqual(_RecordingAuthProxyHandler.received[0]["target"], "syslog")
        self.assertIn("job=job-a", _RecordingAuthProxyHandler.received[0]["text"])

    def test_fail_open_on_unreachable_auth_proxy(self) -> None:
        # Port 1 refuses connections immediately -- no real network call
        # ever leaves the sandbox, and this must not raise (fail-open).
        notifier = build_syslog_notifier("http://127.0.0.1:1", "test-token")
        result = JobRunResult(job_name="job-a", status="failed", reason="boom", duration_seconds=1.0, attempts=1)

        notifier(result)  # must not raise

        self.assertEqual(_RecordingAuthProxyHandler.received, [])

    def test_fail_open_on_unexpected_exception(self) -> None:
        notifier = build_syslog_notifier(f"http://127.0.0.1:{self.port}", "test-token")
        result = JobRunResult(job_name="job-a", status="succeeded", duration_seconds=1.0, attempts=1)

        with mock.patch(
            "shichimimi_agent.scheduler.notify.SlackNotifyClient.notify", side_effect=ValueError("boom")
        ):
            notifier(result)  # must not raise even for a non-SlackNotifyError bug

        self.assertEqual(_RecordingAuthProxyHandler.received, [])


if __name__ == "__main__":
    unittest.main()
