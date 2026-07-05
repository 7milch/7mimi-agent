"""ADR-028: unit coverage for the X_MCP_DIRECT=1 direct-/mcp path, which had
no test coverage at all prior to this file:

- mcp_session.issue_session error handling (HTTP errors, malformed JSON,
  missing fields in the response payload).
- build_direct_mcp_config's exact JSON shape (matches the spike-proven
  Claude Code --mcp-config schema: type "http", url, headers.Authorization).
- build_digest_prompt(direct=True) cost guardrails (max 12 searches,
  max_results<=10, no retry) plus the existing prompt invariants.
- run_claude_digest's X_MCP_DIRECT=1 branch: mints a session token via
  issue_session, skips collect_signals entirely, writes no signals.json,
  and passes DIRECT_MCP_ALLOWED_TOOLS + the mcp_config through to the
  docker command.
- Regression: the non-direct path is unaffected (byte-identical prompt
  and allowed_tools) when X_MCP_DIRECT is unset.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import argparse

from shichimimi_agent.cli import cmd_claude_digest
from shichimimi_agent.config import load_config
from shichimimi_agent.db import Repository, migrate
from shichimimi_agent.proxies.auth_proxy_client import AuthProxyClient
from shichimimi_agent.runner.claude_digest import (
    DEFAULT_ALLOWED_TOOLS,
    DIRECT_MCP_ALLOWED_TOOLS,
    ClaudeDigestOptions,
    ClaudeDigestResult,
    build_digest_prompt,
    build_direct_mcp_config,
    build_docker_command,
    run_claude_digest,
)
from shichimimi_agent.runner.mcp_session import IssuedSession, McpSessionError, issue_session
from shichimimi_agent.security.policy_engine import PolicyEngine
from shichimimi_agent.sessions.workspace import create_workspace


class IssueSessionErrorHandlingTest(unittest.TestCase):
    """mcp_session.issue_session: error paths not covered by the Go
    integration test (which only exercises the happy path + wrong bearer)."""

    def test_url_error_wraps_as_mcp_session_error(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
            with self.assertRaises(McpSessionError) as ctx:
                issue_session(auth_proxy_url="http://127.0.0.1:1", static_token="tok", role="ai_it_topic_runner")
        self.assertIn("connection refused", str(ctx.exception))

    def test_http_error_wraps_as_mcp_session_error_with_code(self) -> None:
        err = urllib.error.HTTPError("http://x", 500, "boom", None, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(McpSessionError) as ctx:
                issue_session(auth_proxy_url="http://127.0.0.1:1", static_token="tok", role="ai_it_topic_runner")
        self.assertIn("500", str(ctx.exception))

    def test_invalid_json_response_raises(self) -> None:
        class _Resp:
            def read(self_inner):
                return b"not json"

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=_Resp()):
            with self.assertRaises(McpSessionError):
                issue_session(auth_proxy_url="http://127.0.0.1:1", static_token="tok", role="ai_it_topic_runner")

    def test_missing_token_field_raises(self) -> None:
        class _Resp:
            def read(self_inner):
                return json.dumps({"ttl_seconds": 2100}).encode("utf-8")

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=_Resp()):
            with self.assertRaises(McpSessionError) as ctx:
                issue_session(auth_proxy_url="http://127.0.0.1:1", static_token="tok", role="ai_it_topic_runner")
        self.assertIn("unexpected payload", str(ctx.exception))

    def test_non_int_ttl_field_raises(self) -> None:
        class _Resp:
            def read(self_inner):
                return json.dumps({"token": "abc", "ttl_seconds": "2100"}).encode("utf-8")

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=_Resp()):
            with self.assertRaises(McpSessionError):
                issue_session(auth_proxy_url="http://127.0.0.1:1", static_token="tok", role="ai_it_topic_runner")

    def test_happy_path_returns_issued_session(self) -> None:
        class _Resp:
            def read(self_inner):
                return json.dumps({"token": "sess-abc", "ttl_seconds": 2100}).encode("utf-8")

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        captured = {}

        def _fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _Resp()

        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            issued = issue_session(auth_proxy_url="http://auth-proxy:18081", static_token="static-tok", role="ai_it_topic_runner")

        self.assertEqual(issued, IssuedSession(token="sess-abc", ttl_seconds=2100))
        self.assertEqual(captured["url"], "http://auth-proxy:18081/session/issue")
        self.assertEqual(captured["headers"]["authorization"], "Bearer static-tok")
        self.assertEqual(captured["body"], {"role": "ai_it_topic_runner"})


class BuildDirectMcpConfigShapeTest(unittest.TestCase):
    """The Claude Code --mcp-config JSON must match the spike-proven schema
    exactly: mcpServers.<name>.{type: "http", url, headers.Authorization}."""

    def test_shape_matches_spike_proven_schema(self) -> None:
        self._env_backup = dict(os.environ)
        os.environ.pop("RUNNER_NETWORK", None)
        os.environ.pop("RUNNER_MCP_URL", None)
        try:
            config = build_direct_mcp_config(session_token="sess-tok-123")
        finally:
            os.environ.clear()
            os.environ.update(self._env_backup)

        self.assertEqual(
            config,
            {
                "mcpServers": {
                    "x7mimi": {
                        "type": "http",
                        "url": "http://host.docker.internal:18081/mcp",
                        "headers": {"Authorization": "Bearer sess-tok-123"},
                    }
                }
            },
        )
        # Must be valid, round-trippable JSON (this is literally what gets
        # written to .mcp.json).
        json.loads(json.dumps(config))

    def test_url_uses_auth_proxy_service_name_on_runner_network(self) -> None:
        self._env_backup = dict(os.environ)
        os.environ["RUNNER_NETWORK"] = "7mimi-internal"
        os.environ.pop("RUNNER_MCP_URL", None)
        try:
            config = build_direct_mcp_config(session_token="sess-tok-123")
        finally:
            os.environ.clear()
            os.environ.update(self._env_backup)
        self.assertEqual(config["mcpServers"]["x7mimi"]["url"], "http://auth-proxy:18081/mcp")

    def test_runner_mcp_url_override_wins(self) -> None:
        self._env_backup = dict(os.environ)
        os.environ["RUNNER_MCP_URL"] = "http://custom-host:9999/mcp"
        try:
            config = build_direct_mcp_config(session_token="sess-tok-123")
        finally:
            os.environ.clear()
            os.environ.update(self._env_backup)
        self.assertEqual(config["mcpServers"]["x7mimi"]["url"], "http://custom-host:9999/mcp")


class DirectPromptCostGuardrailsTest(unittest.TestCase):
    def test_direct_prompt_has_cost_guardrails_and_invariants(self) -> None:
        prompt = build_digest_prompt(
            notes_repo="nishiog/ai-it-research-notes",
            target_relative_path="daily/2026/07/2026-07-05.md",
            git_proxy_url="http://auth-proxy:18081",
            direct=True,
        )
        # Cost guardrails (ADR-028): max 12 searches total, max_results<=10,
        # no retry of the same query.
        self.assertIn("最大 12 回", prompt)
        self.assertIn("max_results", prompt)
        self.assertIn("10 以下", prompt)
        self.assertIn("再試行", prompt)
        self.assertIn("禁止", prompt)
        # tools/list-first guidance so the model discovers what's actually
        # allowed rather than guessing tool names.
        self.assertIn("tools/list", prompt)
        # prompt-injection resistance invariant carries over to the direct
        # variant (X posts are untrusted input either way).
        self.assertIn("prompt injection", prompt)
        # existing invariants (signal vs evidence, no investment advice,
        # Tips section) must still be present in the direct variant.
        self.assertIn("signal であり", prompt)
        self.assertIn("投資助言を書かないこと", prompt)
        self.assertIn("## Tips & 実用例", prompt)
        # direct variant must not reference the pre-collected signals.json
        # (that's the non-direct-only artifact).
        self.assertNotIn("signals.json", prompt)

    def test_non_direct_prompt_unaffected_regression(self) -> None:
        # Byte-identical to what build_digest_prompt(direct=False) (the
        # default) produced before ADR-028: still references signals.json,
        # no direct-mode guardrail text.
        prompt = build_digest_prompt(
            notes_repo="nishiog/ai-it-research-notes",
            target_relative_path="daily/2026/07/2026-07-05.md",
            git_proxy_url="http://auth-proxy:18081",
        )
        self.assertIn("signals.json", prompt)
        self.assertNotIn("最大 12 回", prompt)
        self.assertNotIn("tools/list", prompt)
        # existing invariants preserved.
        self.assertIn("signal であり", prompt)
        self.assertIn("投資助言を書かないこと", prompt)
        self.assertIn("## Tips & 実用例", prompt)


class DirectModeDockerCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self._tmpdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmpdir.name)
        self._env_backup = dict(os.environ)
        os.environ["CLAUDE_PROXY_URL"] = "http://host.docker.internal:18080"
        os.environ["CLAUDE_PROXY_SESSION_TOKEN"] = "cp-sess-secret"
        os.environ["GIT_PROXY_URL"] = "http://host.docker.internal:18081"
        os.environ["GIT_PROXY_SESSION_TOKEN"] = "gp-sess-secret"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_mcp_config_written_and_flags_present(self) -> None:
        mcp_config = build_direct_mcp_config(session_token="sess-tok-xyz")
        cmd = build_docker_command(
            workspace=self.workspace,
            session_id="sess1",
            role="ai_it_topic_runner",
            prompt="prompt text",
            options=ClaudeDigestOptions(),
            allowed_tools=DIRECT_MCP_ALLOWED_TOOLS,
            mcp_config=mcp_config,
        )
        self.assertIn("--mcp-config", cmd)
        idx = cmd.index("--mcp-config")
        self.assertEqual(cmd[idx + 1], "/workspace/.mcp.json")
        self.assertIn("--strict-mcp-config", cmd)

        written = json.loads((self.workspace / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(written, mcp_config)

        self.assertIn("--allowedTools", cmd)
        allowed_idx = cmd.index("--allowedTools")
        self.assertEqual(cmd[allowed_idx + 1], DIRECT_MCP_ALLOWED_TOOLS)
        # the minted session token must never leak into the docker command
        # args themselves (it's only written to .mcp.json inside the mounted
        # workspace, not passed as -e or CLI arg).
        self.assertNotIn("sess-tok-xyz", cmd)

    def test_no_mcp_config_omits_flags(self) -> None:
        cmd = build_docker_command(
            workspace=self.workspace,
            session_id="sess1",
            role="ai_it_topic_runner",
            prompt="prompt text",
            options=ClaudeDigestOptions(),
        )
        self.assertNotIn("--mcp-config", cmd)
        self.assertNotIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], DEFAULT_ALLOWED_TOOLS)
        self.assertFalse((self.workspace / ".mcp.json").exists())


class RunClaudeDigestDirectBranchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.config_obj = load_config(self.root)
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "app.sqlite"
        migrate(db_path)
        self.repository = Repository(db_path)

        self._session_id_for_cleanup = "test-direct-mode-" + next(tempfile._get_candidate_names())
        self.workspace_dir = create_workspace(self.root, self._session_id_for_cleanup)

        self._env_backup = dict(os.environ)
        os.environ["X_MCP_DIRECT"] = "1"
        os.environ["X_MCP_URL"] = "http://auth-proxy:18081"
        os.environ["X_MCP_SESSION_TOKEN"] = "static-admin-token"
        os.environ["CLAUDE_PROXY_URL"] = "http://host.docker.internal:18080"
        os.environ["CLAUDE_PROXY_SESSION_TOKEN"] = "cp-sess-secret"
        os.environ["GIT_PROXY_URL"] = "http://host.docker.internal:18081"
        os.environ["GIT_PROXY_SESSION_TOKEN"] = "gp-sess-secret"

        self.job = {"inputs": {"query_set": "ai_it_watch"}}

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        import shutil

        shutil.rmtree(self.root / ".sessions" / self._session_id_for_cleanup, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_direct_mode_mints_token_skips_collect_signals_no_signals_json(self) -> None:
        with mock.patch("shichimimi_agent.runner.claude_digest.issue_session") as issue_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest.collect_signals") as collect_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest.subprocess.run") as run_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest._verify_published") as verify_mock:
            issue_mock.return_value = IssuedSession(token="minted-sess-tok", ttl_seconds=2100)
            run_mock.return_value = subprocess.CompletedProcess(args=["docker"], returncode=0, stdout="{}", stderr="")
            verify_mock.return_value = (True, "a" * 40)

            result = run_claude_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess-direct-1",
                task_id="task-direct-1",
                workspace=self.workspace_dir,
                job=self.job,
                options=ClaudeDigestOptions(),
                auth_client=AuthProxyClient(local_fallback_engine=PolicyEngine(self.config_obj.policy)),
            )

        # issue_session called once, with the static admin token/role, not
        # collect_signals.
        issue_mock.assert_called_once()
        _, kwargs = issue_mock.call_args
        self.assertEqual(kwargs["auth_proxy_url"], "http://auth-proxy:18081")
        self.assertEqual(kwargs["static_token"], "static-admin-token")
        self.assertEqual(kwargs["role"], "ai_it_topic_runner")
        collect_mock.assert_not_called()

        self.assertFalse((self.workspace_dir / "signals.json").exists())
        self.assertTrue((self.workspace_dir / ".mcp.json").exists())
        written = json.loads((self.workspace_dir / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(written["mcpServers"]["x7mimi"]["headers"]["Authorization"], "Bearer minted-sess-tok")

        docker_cmd = run_mock.call_args.args[0]
        allowed_idx = docker_cmd.index("--allowedTools")
        self.assertEqual(docker_cmd[allowed_idx + 1], DIRECT_MCP_ALLOWED_TOOLS)
        self.assertIn("--mcp-config", docker_cmd)

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.verified)

    def test_direct_mode_missing_x_mcp_url_raises(self) -> None:
        del os.environ["X_MCP_URL"]
        with self.assertRaises(ValueError):
            run_claude_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess-direct-2",
                task_id="task-direct-2",
                workspace=self.workspace_dir,
                job=self.job,
                options=ClaudeDigestOptions(),
                auth_client=AuthProxyClient(local_fallback_engine=PolicyEngine(self.config_obj.policy)),
            )

    def test_direct_mode_missing_x_mcp_session_token_raises(self) -> None:
        del os.environ["X_MCP_SESSION_TOKEN"]
        with self.assertRaises(ValueError):
            run_claude_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess-direct-3",
                task_id="task-direct-3",
                workspace=self.workspace_dir,
                job=self.job,
                options=ClaudeDigestOptions(),
                auth_client=AuthProxyClient(local_fallback_engine=PolicyEngine(self.config_obj.policy)),
            )


class CliDirectFlagTest(unittest.TestCase):
    """cli.cmd_claude_digest: --direct sets X_MCP_DIRECT=1 in the process
    environment before delegating to run_claude_digest (no prior test
    covered this flag at all)."""

    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self._env_backup = dict(os.environ)
        os.environ.pop("X_MCP_DIRECT", None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_direct_flag_sets_x_mcp_direct_env_before_run(self) -> None:
        args = argparse.Namespace(root=str(self.root), job="ai-it-x-daily-digest", model=None, max_turns=40, direct=True)

        observed_env = {}

        def _fake_run_claude_digest(**kwargs):
            observed_env["X_MCP_DIRECT"] = os.environ.get("X_MCP_DIRECT")
            return ClaudeDigestResult(
                exit_code=0,
                stdout="{}",
                stderr="",
                workspace=self.root,
                verified=True,
                verified_path="daily/x.md",
                commit_sha="a" * 40,
            )

        with mock.patch(
            "shichimimi_agent.runner.claude_digest.run_claude_digest", side_effect=_fake_run_claude_digest
        ):
            cmd_claude_digest(args)

        self.assertEqual(observed_env["X_MCP_DIRECT"], "1")

    def test_no_direct_flag_leaves_x_mcp_direct_unset(self) -> None:
        args = argparse.Namespace(root=str(self.root), job="ai-it-x-daily-digest", model=None, max_turns=40, direct=False)

        observed_env = {}

        def _fake_run_claude_digest(**kwargs):
            observed_env["X_MCP_DIRECT"] = os.environ.get("X_MCP_DIRECT")
            return ClaudeDigestResult(
                exit_code=0,
                stdout="{}",
                stderr="",
                workspace=self.root,
                verified=True,
                verified_path="daily/x.md",
                commit_sha="a" * 40,
            )

        with mock.patch(
            "shichimimi_agent.runner.claude_digest.run_claude_digest", side_effect=_fake_run_claude_digest
        ):
            cmd_claude_digest(args)

        self.assertIsNone(observed_env["X_MCP_DIRECT"])


if __name__ == "__main__":
    unittest.main()
