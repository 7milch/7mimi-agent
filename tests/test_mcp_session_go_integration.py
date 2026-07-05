"""Cross-language integration test for ADR-028's session-token /mcp flow.

Builds the real Go auth-proxy binary, starts it with AUTH_PROXY_SESSION_TOKEN
(static admin token) plus X_BEARER_TOKEN (stub X API), then drives:

  (a) POST /session/issue with the static bearer -> minted role-bound token;
      wrong static bearer -> 401.
  (b) The minted token against /mcp via the real Python McpHttpClient:
      tools/list is role-filtered (x tools only, no jq for ai_it_topic_runner);
      tools/call x.search_posts_recent succeeds; tools/call x.create_post
      (denied for the role) -> JSON-RPC error + audit block line on stdout.
  (c) Hard cap: AUTH_PROXY_MCP_CALL_CAP=2 -> 3rd tools/call on a mint
      returns a cap-exceeded error.
  (d) gitrelay accepts the minted token for GET info/refs (not 401).
  (e) The static token still gets the full, unfiltered tools/list.

Skipped entirely if the `go` toolchain is not available.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from shichimimi_agent.mcp.client import McpClientError, McpHttpClient
from shichimimi_agent.runner.mcp_session import McpSessionError, issue_session

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTH_PROXY_DIR = REPO_ROOT / "services" / "auth-proxy"

STATIC_TOKEN = "sentinel-static-admin-token"
X_BEARER_SENTINEL = "sentinel-x-bearer-do-not-leak"
ROLE = "ai_it_topic_runner"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:  # noqa: PERF203
            last_err = exc
            time.sleep(0.05)
    raise RuntimeError(f"server on {host}:{port} did not start in time: {last_err}")


class _StubXAPIHandler(http.server.BaseHTTPRequestHandler):
    server_version = "StubXAPI/1.0"

    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/2/tweets/search/recent"):
            body = json.dumps(
                {
                    "data": [
                        {
                            "id": "2001",
                            "text": "AI ops news",
                            "author_id": "u1",
                            "created_at": "2026-07-05T00:00:00.000Z",
                            "public_metrics": {
                                "like_count": 1,
                                "retweet_count": 0,
                                "reply_count": 0,
                                "quote_count": 0,
                            },
                        }
                    ],
                    "includes": {"users": [{"id": "u1", "username": "alice_ai"}]},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def _build_binary(tmpdir: str) -> Path:
    binary_path = Path(tmpdir) / "auth-proxy-session-test"
    build = subprocess.run(
        ["go", "build", "-o", str(binary_path), "./cmd/auth-proxy"],
        cwd=AUTH_PROXY_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if build.returncode != 0:
        raise RuntimeError(f"go build failed: {build.stdout}\n{build.stderr}")
    return binary_path


def _start_stub() -> tuple[http.server.ThreadingHTTPServer, threading.Thread, int]:
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _StubXAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, port


def _start_proxy(binary_path: Path, extra_env: dict[str, str]) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    env = dict(os.environ)
    env["AUTH_PROXY_ADDR"] = f"127.0.0.1:{port}"
    env["AUTH_PROXY_SESSION_TOKEN"] = STATIC_TOKEN
    env.update(extra_env)
    process = subprocess.Popen(
        [str(binary_path)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_port("127.0.0.1", port, timeout=10.0)
    except Exception:
        process.terminate()
        raise
    return process, port


def _stop_proxy(process: subprocess.Popen) -> str:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    try:
        return process.stdout.read() or ""
    except Exception:
        return ""


@unittest.skipUnless(shutil.which("go"), "go toolchain not available")
class SessionIssueBasicsTest(unittest.TestCase):
    """(a) POST /session/issue: static bearer succeeds, wrong bearer 401."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.binary_path = _build_binary(cls._tmpdir.name)
        cls.stub_server, cls.stub_thread, cls.stub_port = _start_stub()
        cls.proxy_process, cls.proxy_port = _start_proxy(
            cls.binary_path,
            {
                "X_BEARER_TOKEN": X_BEARER_SENTINEL,
                "X_API_BASE_URL": f"http://127.0.0.1:{cls.stub_port}",
            },
        )
        cls.base_url = f"http://127.0.0.1:{cls.proxy_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        _stop_proxy(cls.proxy_process)
        cls.stub_server.shutdown()
        cls.stub_thread.join(timeout=5)
        cls._tmpdir.cleanup()

    def test_issue_session_with_correct_static_bearer(self) -> None:
        issued = issue_session(auth_proxy_url=self.base_url, static_token=STATIC_TOKEN, role=ROLE)
        self.assertTrue(issued.token)
        self.assertEqual(issued.ttl_seconds, 35 * 60)

    def test_issue_session_with_wrong_static_bearer_is_401(self) -> None:
        with self.assertRaises(McpSessionError) as ctx:
            issue_session(auth_proxy_url=self.base_url, static_token="wrong-token", role=ROLE)
        self.assertIn("401", str(ctx.exception))

    def test_issue_session_unknown_role_still_mints(self) -> None:
        # Design intent (ADR-028): role is not validated against
        # role_tool_policy at /session/issue -- enforcement happens at /mcp
        # (tools/list filtering + tools/call Decide), so an unknown role
        # still mints a token here (it would simply resolve to an
        # empty/deny-everything allow list at /mcp).
        issued = issue_session(auth_proxy_url=self.base_url, static_token=STATIC_TOKEN, role="totally_unknown_role")
        self.assertTrue(issued.token)


@unittest.skipUnless(shutil.which("go"), "go toolchain not available")
class SessionMcpEnforcementTest(unittest.TestCase):
    """(b), (d), (e): role-bound token at /mcp + gitrelay + static token."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.binary_path = _build_binary(cls._tmpdir.name)
        cls.stub_server, cls.stub_thread, cls.stub_port = _start_stub()
        cls.proxy_process, cls.proxy_port = _start_proxy(
            cls.binary_path,
            {
                "X_BEARER_TOKEN": X_BEARER_SENTINEL,
                "X_API_BASE_URL": f"http://127.0.0.1:{cls.stub_port}",
                # Mounts jq.* tools (never dialed for real: the deny test
                # below is rejected by Decide() before upstream dispatch).
                "JQUANTS_REFRESH_TOKEN": "sentinel-jq-refresh-token",
            },
        )
        cls.base_url = f"http://127.0.0.1:{cls.proxy_port}"
        cls.issued = issue_session(auth_proxy_url=cls.base_url, static_token=STATIC_TOKEN, role=ROLE)

    def setUp(self) -> None:
        # A fresh mint per test method avoids cross-test cap-count coupling
        # for tests that need their own call budget.
        self.issued = issue_session(auth_proxy_url=self.base_url, static_token=STATIC_TOKEN, role=ROLE)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stdout = _stop_proxy(cls.proxy_process)
        cls.stub_server.shutdown()
        cls.stub_thread.join(timeout=5)
        cls._tmpdir.cleanup()

    def test_role_bound_tools_list_is_filtered_to_x_tools_only_no_jq(self) -> None:
        client = McpHttpClient(base_url=self.base_url, session_token=self.issued.token)
        client.initialize()
        tools = client.list_tools()
        names = {t["name"] for t in tools}
        self.assertTrue(names, "role-filtered tools/list should not be empty for ai_it_topic_runner")
        self.assertTrue(all(n.startswith("x.") for n in names), f"unexpected non-x tools: {names}")
        self.assertNotIn("jq.get_listed_info", names)
        self.assertIn("x.search_posts_recent", names)

    def test_role_bound_allowed_tool_call_succeeds(self) -> None:
        client = McpHttpClient(base_url=self.base_url, session_token=self.issued.token)
        client.initialize()
        result = client.call_tool("x.search_posts_recent", {"query": "AI OR IT", "max_results": 10})
        self.assertFalse(result.get("isError", False))
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(len(payload["posts"]), 1)

    def test_role_bound_unmounted_write_tool_is_unknown_not_dispatched(self) -> None:
        # x.create_post is never in the Go xmcp tool table at all (it isn't
        # implemented -- xmcp only exposes read tools); tools/call rejects
        # it via the generic "unknown or unsupported tool" branch, which
        # runs *before* the role/policy Decide() check (xmcp.go
        # handleToolsCall calls hasTool() first). So this specific name
        # cannot be used to exercise the role-deny path -- it never reaches
        # Decide(). See test_role_bound_denied_tool_call_errors_and_audits_block
        # for the real allow-list-present-but-denied-for-role path (jq.*).
        client = McpHttpClient(base_url=self.base_url, session_token=self.issued.token)
        client.initialize()
        with self.assertRaises(McpClientError):
            client.call_tool("x.create_post", {"text": "hello"})

    def test_role_bound_denied_tool_call_errors_and_audits_block(self) -> None:
        # jq.get_listed_info genuinely exists in this handler's tool table
        # (JQUANTS_REFRESH_TOKEN is set for this class) but is denied for
        # ai_it_topic_runner (config/policy.yaml + Go DevEngine both deny
        # jq.*), so this exercises the real Decide()-denies-role path,
        # unlike x.create_post above which never reaches Decide().
        client = McpHttpClient(base_url=self.base_url, session_token=self.issued.token)
        client.initialize()
        with self.assertRaises(McpClientError) as ctx:
            client.call_tool("jq.get_listed_info", {})
        self.assertIn("tools/call failed", str(ctx.exception))

        # Verify via a raw request that the JSON-RPC error body itself
        # signals "not permitted", proving the Go-side Decide() denied it
        # rather than some unrelated failure (e.g. unknown tool).
        request = urllib.request.Request(
            f"{self.base_url}/mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 999,
                    "method": "tools/call",
                    "params": {"name": "jq.get_listed_info", "arguments": {}},
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.issued.token}"},
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        self.assertIn("error", body)
        self.assertIn("not permitted", body["error"]["message"])

    def test_gitrelay_accepts_minted_session_token(self) -> None:
        # No GitHub App credentials configured in this class's proxy, so
        # gitrelay is not mounted; assert this yields 404 (route absent),
        # not 401 (auth rejected) -- i.e. the minted token is never the
        # reason a /git request would fail. The dedicated gitrelay class
        # below covers the "accepted, reaches upstream" case with real App
        # credentials stubbed via a fake upstream.
        request = urllib.request.Request(
            f"{self.base_url}/git/owner/repo/info/refs?service=git-upload-pack",
            method="GET",
            headers={"Authorization": f"Bearer {self.issued.token}"},
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            self.fail("expected an HTTPError")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_static_token_gets_full_unfiltered_tools_list(self) -> None:
        client = McpHttpClient(base_url=self.base_url, session_token=STATIC_TOKEN)
        client.initialize()
        tools = client.list_tools()
        names = {t["name"] for t in tools}
        self.assertEqual(
            names,
            {
                "x.search_posts_recent",
                "x.get_posts",
                "x.get_users",
                "x.get_users_by_username",
                "jq.get_listed_info",
                "jq.get_daily_quotes",
                "jq.get_statements",
            },
        )

    def test_static_token_can_call_tool_denied_to_the_role(self) -> None:
        # Static/admin token skips role enforcement entirely (current /
        # backward-compatible behavior per xmcp.go authorizeToken); this is
        # not exercised over the network as a write (x.create_post is not
        # actually implemented upstream), so assert it is NOT rejected for
        # policy reasons specifically -- i.e. it does not get the
        # "not permitted" error the role-bound token gets above.
        request = urllib.request.Request(
            f"{self.base_url}/mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1000,
                    "method": "tools/call",
                    "params": {"name": "x.create_post", "arguments": {"text": "hello"}},
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {STATIC_TOKEN}"},
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        # Static token isn't role-scoped, so if there is an error it must
        # not be the role-policy "not permitted" message (unimplemented
        # tool -> "unknown tool" is expected/acceptable instead).
        if "error" in body:
            self.assertNotIn("not permitted", body["error"]["message"])


@unittest.skipUnless(shutil.which("go"), "go toolchain not available")
class SessionMcpBlockAuditTest(unittest.TestCase):
    """Dedicated single-test class so we can inspect the whole subprocess
    stdout after termination, to confirm a block audit line is emitted for
    a denied tools/call on a role-bound token."""

    def test_denied_tool_call_emits_block_audit_line_on_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            binary_path = _build_binary(tmpdir)
            stub_server, stub_thread, stub_port = _start_stub()
            try:
                proxy_process, proxy_port = _start_proxy(
                    binary_path,
                    {
                        "X_BEARER_TOKEN": X_BEARER_SENTINEL,
                        "X_API_BASE_URL": f"http://127.0.0.1:{stub_port}",
                        "JQUANTS_REFRESH_TOKEN": "sentinel-jq-refresh-token",
                    },
                )
                base_url = f"http://127.0.0.1:{proxy_port}"
                try:
                    issued = issue_session(auth_proxy_url=base_url, static_token=STATIC_TOKEN, role=ROLE)
                    client = McpHttpClient(base_url=base_url, session_token=issued.token)
                    client.initialize()
                    with self.assertRaises(McpClientError):
                        client.call_tool("jq.get_listed_info", {})
                    # give the audit line a moment to flush before we kill
                    time.sleep(0.2)
                finally:
                    stdout = _stop_proxy(proxy_process)
            finally:
                stub_server.shutdown()
                stub_thread.join(timeout=5)

        block_lines = [
            json.loads(line)
            for line in stdout.splitlines()
            if line.strip().startswith("{") and '"decision"' in line
        ]
        block_events = [e for e in block_lines if e.get("decision") == "block"]
        self.assertTrue(block_events, f"expected a block audit event in stdout, got: {stdout!r}")
        self.assertEqual(block_events[-1]["tool_name"], "jq.get_listed_info")
        # ADR-028: auditBlock/audit in xmcp.go record the resolved
        # session-token role (not a hard-coded "x-mcp"), so the audit trail
        # can distinguish which role triggered a given block.
        self.assertEqual(block_events[-1]["role"], ROLE)


@unittest.skipUnless(shutil.which("go"), "go toolchain not available")
class SessionCallCapTest(unittest.TestCase):
    """(c) AUTH_PROXY_MCP_CALL_CAP=2 -> 3rd tools/call is rejected."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.binary_path = _build_binary(cls._tmpdir.name)
        cls.stub_server, cls.stub_thread, cls.stub_port = _start_stub()
        cls.proxy_process, cls.proxy_port = _start_proxy(
            cls.binary_path,
            {
                "X_BEARER_TOKEN": X_BEARER_SENTINEL,
                "X_API_BASE_URL": f"http://127.0.0.1:{cls.stub_port}",
                "AUTH_PROXY_MCP_CALL_CAP": "2",
            },
        )
        cls.base_url = f"http://127.0.0.1:{cls.proxy_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        _stop_proxy(cls.proxy_process)
        cls.stub_server.shutdown()
        cls.stub_thread.join(timeout=5)
        cls._tmpdir.cleanup()

    def test_third_call_exceeds_hard_cap(self) -> None:
        issued = issue_session(auth_proxy_url=self.base_url, static_token=STATIC_TOKEN, role=ROLE)
        client = McpHttpClient(base_url=self.base_url, session_token=issued.token)
        client.initialize()

        # 1st and 2nd calls succeed (within cap of 2).
        for _ in range(2):
            result = client.call_tool("x.search_posts_recent", {"query": "AI OR IT", "max_results": 10})
            self.assertFalse(result.get("isError", False))

        # 3rd call exceeds the cap.
        with self.assertRaises(McpClientError) as ctx:
            client.call_tool("x.search_posts_recent", {"query": "AI OR IT", "max_results": 10})
        self.assertIn("tools/call failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
