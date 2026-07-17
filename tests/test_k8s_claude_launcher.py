"""Unit tests for KubernetesClaudeLauncher (Issue #31: claude CLI k8s Job,
replacing the nested `docker run` claude_digest.py used before -- k3s
scheduler Pods have no docker).

Mirrors the structure of test_kubernetes_runner.py: the HTTP layer is
exercised by mocking `_api_request` / `_wait_for_completion` (the seams
KubernetesApiClientMixin was split out for), never real network calls.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shichimimi_agent.runner.claude_digest import ClaudeDigestOptions, ClaudeInvocation, error_excerpt
from shichimimi_agent.runner.k8s_claude_launcher import (
    STDERR_FILENAME,
    STDOUT_FILENAME,
    KubernetesClaudeLauncher,
    KubernetesClaudeLauncherOptions,
)


def _options(**overrides) -> KubernetesClaudeLauncherOptions:
    kwargs = dict(
        namespace="test-ns",
        pvc_name="test-pvc",
        node_hostname="test-node",
        image_pull_secret="test-pull-secret",
        runner_label="test-runner-label",
        poll_interval_seconds=0.0,
        timeout_seconds=5.0,
    )
    kwargs.update(overrides)
    return KubernetesClaudeLauncherOptions(**kwargs)


def _invocation(**overrides) -> ClaudeInvocation:
    kwargs = dict(
        prompt="do the digest, and don't leak secrets",
        allowed_tools="Read,Write,WebFetch,Bash(git:*)",
        extra_args=["--mcp-config", "/workspace/.mcp.json", "--strict-mcp-config", "--max-turns", "40", "--output-format", "json"],
        env={
            "SESSION_ID": "sess1",
            "ROLE": "ai_it_topic_runner",
            "ANTHROPIC_BASE_URL": "http://claude-proxy:18080",
            "ANTHROPIC_AUTH_TOKEN": "cp-sess-secret",
            "ANTHROPIC_MODEL": "claude-sonnet-5",
            "HOME": "/workspace",
        },
    )
    kwargs.update(overrides)
    return ClaudeInvocation(**kwargs)


class JobManifestCredentialTest(unittest.TestCase):
    """No provider credential (ANTHROPIC_API_KEY etc.) may ever reach the Job
    manifest -- only the short-lived claude-proxy session token, matching
    the docker launcher's invariant (test_claude_digest.py)."""

    def test_no_provider_credential_leaks_into_manifest(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        invocation = _invocation(
            env={
                "SESSION_ID": "sess1",
                "ROLE": "ai_it_topic_runner",
                "ANTHROPIC_BASE_URL": "http://claude-proxy:18080",
                "ANTHROPIC_AUTH_TOKEN": "cp-sess-secret",
            }
        )
        with mock.patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-real-secret",
                "SHICHIMIMI_AGENT_X_BEARER_TOKEN": "x-secret-token",
                "GITHUB_TOKEN": "ghp_secret",
            },
        ):
            manifest = launcher._job_manifest(
                session_id="sess1", role="ai_it_topic_runner", invocation=invocation, timeout_seconds=1200
            )
        serialized = json.dumps(manifest)
        for leak in ("sk-ant-real-secret", "ANTHROPIC_API_KEY", "x-secret-token", "SHICHIMIMI_AGENT_X_BEARER_TOKEN", "ghp_secret", "GITHUB_TOKEN"):
            self.assertNotIn(leak, serialized)
        # The claude-proxy session token itself IS expected (short-lived,
        # role-scoped -- same as the docker launcher).
        self.assertIn("cp-sess-secret", serialized)


class JobManifestHardeningTest(unittest.TestCase):
    """Mirrors KubernetesRunnerBackend's hardening (kubernetes_runner.py /
    test_kubernetes_runner.py RunnerJobHardeningTest)."""

    def _manifest(self, **option_overrides):
        launcher = KubernetesClaudeLauncher(options=_options(**option_overrides))
        return launcher, launcher._job_manifest(
            session_id="sess_abc", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=1200
        )

    def test_backoff_limit_zero_and_restart_policy_never(self) -> None:
        _, manifest = self._manifest()
        self.assertEqual(manifest["spec"]["backoffLimit"], 0)
        self.assertEqual(manifest["spec"]["template"]["spec"]["restartPolicy"], "Never")

    def test_ttl_seconds_after_finished_set_from_options(self) -> None:
        _, manifest = self._manifest(ttl_seconds_after_finished=42)
        self.assertEqual(manifest["spec"]["ttlSecondsAfterFinished"], 42)

    def test_runner_label_present_and_matches_networkpolicy_selector(self) -> None:
        """deploy/k8s/networkpolicy.yaml selects app.kubernetes.io/name:
        7mimi-agent-runner -- the launcher's default must match exactly."""
        launcher = KubernetesClaudeLauncher(options=_options(runner_label=KubernetesClaudeLauncherOptions().runner_label))
        manifest = launcher._job_manifest(
            session_id="sess1", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=1200
        )
        self.assertEqual(manifest["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"], "7mimi-agent-runner")

    def test_node_selector_pins_to_configured_hostname(self) -> None:
        _, manifest = self._manifest()
        self.assertEqual(
            manifest["spec"]["template"]["spec"]["nodeSelector"], {"kubernetes.io/hostname": "test-node"}
        )

    def test_image_pull_secret_present(self) -> None:
        _, manifest = self._manifest()
        self.assertEqual(manifest["spec"]["template"]["spec"]["imagePullSecrets"], [{"name": "test-pull-secret"}])

    def test_no_argocd_instance_tracking_label_anywhere_in_manifest(self) -> None:
        _, manifest = self._manifest()
        self.assertNotIn("app.kubernetes.io/instance", json.dumps(manifest))

    def test_automount_service_account_token_is_false(self) -> None:
        _, manifest = self._manifest()
        self.assertIs(manifest["spec"]["template"]["spec"]["automountServiceAccountToken"], False)

    def test_pod_security_context_is_restricted_equivalent(self) -> None:
        _, manifest = self._manifest()
        pod_security = manifest["spec"]["template"]["spec"]["securityContext"]
        self.assertIs(pod_security["runAsNonRoot"], True)
        self.assertEqual(pod_security["seccompProfile"], {"type": "RuntimeDefault"})
        self.assertEqual(pod_security["fsGroup"], pod_security["runAsGroup"])

    def test_run_as_user_and_group_default_to_10001(self) -> None:
        _, manifest = self._manifest()
        pod_security = manifest["spec"]["template"]["spec"]["securityContext"]
        self.assertEqual(pod_security["runAsUser"], 10001)
        self.assertEqual(pod_security["runAsGroup"], 10001)

    def test_container_security_context_drops_all_capabilities(self) -> None:
        _, manifest = self._manifest()
        container_security = manifest["spec"]["template"]["spec"]["containers"][0]["securityContext"]
        self.assertIs(container_security["allowPrivilegeEscalation"], False)
        self.assertEqual(container_security["capabilities"], {"drop": ["ALL"]})

    def test_job_namespace_matches_resolved_namespace(self) -> None:
        _, manifest = self._manifest()
        self.assertEqual(manifest["metadata"]["namespace"], "test-ns")

    def test_job_name_translates_underscores_and_is_lowercase(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        manifest = launcher._job_manifest(
            session_id="sess_ABC_123", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=1200
        )
        name = manifest["metadata"]["name"]
        self.assertEqual(name, name.lower())
        self.assertNotIn("_", name)
        self.assertLessEqual(len(name), 63)
        self.assertFalse(name.endswith("-"))

    def test_active_deadline_seconds_set_from_call_arg(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        manifest = launcher._job_manifest(
            session_id="sess1", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=777
        )
        self.assertEqual(manifest["spec"]["activeDeadlineSeconds"], 777)


class JobManifestWorkspaceMountTest(unittest.TestCase):
    def test_workspace_subpath_mount_and_working_dir(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        manifest = launcher._job_manifest(
            session_id="sess_xyz", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=1200
        )
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["workingDir"], "/workspace")
        mounts = container["volumeMounts"]
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0]["mountPath"], "/workspace")
        self.assertEqual(mounts[0]["subPath"], "sessions/sess_xyz/workspace")

        volumes = manifest["spec"]["template"]["spec"]["volumes"]
        self.assertEqual(len(volumes), 1)
        self.assertEqual(volumes[0]["persistentVolumeClaim"]["claimName"], "test-pvc")

    def test_only_session_workspace_mounted_not_whole_pvc(self) -> None:
        """No mount lacking a subPath -- the whole PVC (other sessions,
        .data/, config/) must never be exposed to the claude-CLI Job."""
        launcher = KubernetesClaudeLauncher(options=_options())
        manifest = launcher._job_manifest(
            session_id="sess1", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=1200
        )
        for mount in manifest["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]:
            self.assertIn("subPath", mount)
            self.assertTrue(mount["subPath"].startswith("sessions/sess1/"))


class CommandInjectionSafetyTest(unittest.TestCase):
    """The prompt / allowedTools carry orchestrator/config-composed
    free-form text and must never be interpolated directly into the sh -c
    script string (only referenced as "$VAR")."""

    def test_prompt_and_allowed_tools_not_inlined_in_script_only_via_env(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        dangerous_prompt = 'do it"; rm -rf / #$(whoami)`id`'
        invocation = _invocation(prompt=dangerous_prompt, allowed_tools="Read,Write")
        manifest = launcher._job_manifest(
            session_id="sess1", role="ai_it_topic_runner", invocation=invocation, timeout_seconds=1200
        )
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        command = container["command"]
        self.assertEqual(command[0], "sh")
        self.assertEqual(command[1], "-c")
        script = command[2]
        # The raw dangerous text must never appear in the script itself...
        self.assertNotIn(dangerous_prompt, script)
        # ...it must be referenced only via the env var.
        self.assertIn('"$CLAUDE_PROMPT"', script)
        self.assertIn('"$CLAUDE_ALLOWED_TOOLS"', script)

        env = {item["name"]: item["value"] for item in container["env"]}
        self.assertEqual(env["CLAUDE_PROMPT"], dangerous_prompt)
        self.assertEqual(env["CLAUDE_ALLOWED_TOOLS"], "Read,Write")

    def test_script_redirects_stdout_and_stderr_to_workspace_files(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        manifest = launcher._job_manifest(
            session_id="sess1", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=1200
        )
        script = manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]
        self.assertIn(f"> /workspace/{STDOUT_FILENAME}", script)
        self.assertIn(f"2> /workspace/{STDERR_FILENAME}", script)

    def test_static_extra_args_inlined_and_safe(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        invocation = _invocation(
            extra_args=["--mcp-config", "/workspace/.mcp.json", "--strict-mcp-config", "--max-turns", "40", "--output-format", "json"]
        )
        manifest = launcher._job_manifest(
            session_id="sess1", role="ai_it_topic_runner", invocation=invocation, timeout_seconds=1200
        )
        script = manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]
        self.assertIn("--mcp-config /workspace/.mcp.json --strict-mcp-config --max-turns 40 --output-format json", script)


class EnvContentTest(unittest.TestCase):
    def test_egress_proxy_env_added_when_runner_egress_proxy_set(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        with mock.patch.dict(os.environ, {"RUNNER_EGRESS_PROXY": "http://egress-proxy:18082"}):
            manifest = launcher._job_manifest(
                session_id="sess1", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=1200
            )
        env = {item["name"]: item["value"] for item in manifest["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["HTTPS_PROXY"], "http://egress-proxy:18082")
        self.assertEqual(env["HTTP_PROXY"], "http://egress-proxy:18082")
        self.assertEqual(env["NO_PROXY"], "claude-proxy,auth-proxy,egress-proxy,localhost,127.0.0.1")

    def test_egress_proxy_env_omitted_when_unset(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RUNNER_EGRESS_PROXY", None)
            manifest = launcher._job_manifest(
                session_id="sess1", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=1200
            )
        env_keys = {item["name"] for item in manifest["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertNotIn("HTTPS_PROXY", env_keys)
        self.assertNotIn("HTTP_PROXY", env_keys)
        self.assertNotIn("NO_PROXY", env_keys)

    def test_base_invocation_env_forwarded(self) -> None:
        launcher = KubernetesClaudeLauncher(options=_options())
        invocation = _invocation(
            env={"SESSION_ID": "sess1", "ROLE": "ai_it_topic_runner", "ANTHROPIC_MODEL": "claude-sonnet-5"}
        )
        manifest = launcher._job_manifest(
            session_id="sess1", role="ai_it_topic_runner", invocation=invocation, timeout_seconds=1200
        )
        env = {item["name"]: item["value"] for item in manifest["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["SESSION_ID"], "sess1")
        self.assertEqual(env["ANTHROPIC_MODEL"], "claude-sonnet-5")


class RunSuccessPathTest(unittest.TestCase):
    def test_reads_stdout_and_stderr_files_from_workspace_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / STDOUT_FILENAME).write_text('{"result": "ok"}', encoding="utf-8")
            (workspace / STDERR_FILENAME).write_text("", encoding="utf-8")

            launcher = KubernetesClaudeLauncher(options=_options())
            api_responses = [{}, {"status": {"succeeded": 1}}]
            with mock.patch.object(launcher, "_api_request", side_effect=api_responses) as api_request:
                exit_code, stdout, stderr = launcher.run(
                    workspace=workspace,
                    session_id="sess1",
                    role="ai_it_topic_runner",
                    invocation=_invocation(),
                    timeout_seconds=1200,
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, '{"result": "ok"}')
        self.assertEqual(stderr, "")
        create_call = api_request.call_args_list[0]
        self.assertEqual(create_call.args[0], "POST")
        self.assertEqual(create_call.args[1], "/apis/batch/v1/namespaces/test-ns/jobs")
        self.assertEqual(create_call.kwargs["body"]["kind"], "Job")

    def test_missing_output_files_yield_empty_strings_not_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            launcher = KubernetesClaudeLauncher(options=_options())
            api_responses = [{}, {"status": {"succeeded": 1}}]
            with mock.patch.object(launcher, "_api_request", side_effect=api_responses):
                exit_code, stdout, stderr = launcher.run(
                    workspace=workspace,
                    session_id="sess1",
                    role="ai_it_topic_runner",
                    invocation=_invocation(),
                    timeout_seconds=1200,
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")


class PollTimeoutBufferTest(unittest.TestCase):
    """Tech-lead review (Issue #31): the scheduler-side poll loop must
    outlive the Job's own activeDeadlineSeconds by a margin (default 60s),
    or our own poll timeout can race and pre-empt k8s's clean
    DeadlineExceeded Job condition."""

    def test_wait_for_completion_gets_buffered_timeout_while_active_deadline_is_unbuffered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            launcher = KubernetesClaudeLauncher(options=_options(poll_timeout_buffer_seconds=60.0))
            with mock.patch.object(launcher, "_api_request", return_value={}), mock.patch.object(
                launcher, "_wait_for_completion"
            ) as wait_mock:
                launcher.run(
                    workspace=workspace,
                    session_id="sess1",
                    role="ai_it_topic_runner",
                    invocation=_invocation(),
                    timeout_seconds=1200,
                )

        wait_mock.assert_called_once()
        _, kwargs = wait_mock.call_args
        # The poll loop's own timeout is buffered...
        self.assertEqual(kwargs["timeout_seconds"], 1260)
        # ...while the Job manifest's activeDeadlineSeconds (the real,
        # k8s-enforced ceiling) is left unbuffered.
        manifest = launcher._job_manifest(
            session_id="sess1", role="ai_it_topic_runner", invocation=_invocation(), timeout_seconds=1200
        )
        self.assertEqual(manifest["spec"]["activeDeadlineSeconds"], 1200)

    def test_buffer_is_configurable_via_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            launcher = KubernetesClaudeLauncher(options=_options(poll_timeout_buffer_seconds=15.0))
            with mock.patch.object(launcher, "_api_request", return_value={}), mock.patch.object(
                launcher, "_wait_for_completion"
            ) as wait_mock:
                launcher.run(
                    workspace=workspace,
                    session_id="sess1",
                    role="ai_it_topic_runner",
                    invocation=_invocation(),
                    timeout_seconds=100,
                )
        _, kwargs = wait_mock.call_args
        self.assertEqual(kwargs["timeout_seconds"], 115)

    def test_default_buffer_is_60_seconds(self) -> None:
        self.assertEqual(KubernetesClaudeLauncherOptions().poll_timeout_buffer_seconds, 60.0)


class RunFailurePathTest(unittest.TestCase):
    def test_job_failure_returns_nonzero_exit_and_includes_stderr_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / STDERR_FILENAME).write_text("claude: authentication failed", encoding="utf-8")

            launcher = KubernetesClaudeLauncher(options=_options())
            api_responses = [
                {},  # POST create job
                {"status": {"failed": 1, "conditions": [{"type": "Failed", "message": "BackoffLimitExceeded"}]}},
            ]
            with mock.patch.object(launcher, "_api_request", side_effect=api_responses):
                exit_code, stdout, stderr = launcher.run(
                    workspace=workspace,
                    session_id="sess1",
                    role="ai_it_topic_runner",
                    invocation=_invocation(),
                    timeout_seconds=1200,
                )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("BackoffLimitExceeded", stderr)
        self.assertIn("claude: authentication failed", stderr)

    def test_job_failure_without_stderr_file_still_returns_job_level_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            launcher = KubernetesClaudeLauncher(options=_options())
            api_responses = [
                {},
                {"status": {"failed": 1, "conditions": [{"type": "Failed", "message": "OOMKilled"}]}},
            ]
            with mock.patch.object(launcher, "_api_request", side_effect=api_responses):
                exit_code, stdout, stderr = launcher.run(
                    workspace=workspace,
                    session_id="sess1",
                    role="ai_it_topic_runner",
                    invocation=_invocation(),
                    timeout_seconds=1200,
                )
        self.assertEqual(exit_code, 1)
        self.assertIn("OOMKilled", stderr)

    def test_timeout_returns_nonzero_exit(self) -> None:
        """run() adds poll_timeout_buffer_seconds (default 60s) on top of
        the caller's timeout_seconds before it ever reaches the poll loop
        (see PollTimeoutBufferTest) -- so the monotonic() sequence here must
        clear timeout_seconds + buffer, not just timeout_seconds, to
        actually trigger the timeout path."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            launcher = KubernetesClaudeLauncher(
                options=_options(timeout_seconds=1.0, poll_interval_seconds=0.0, poll_timeout_buffer_seconds=0.0)
            )
            with mock.patch.object(launcher, "_api_request", return_value={"status": {}}):
                with mock.patch("time.sleep"):
                    with mock.patch("time.monotonic", side_effect=[0.0, 10.0, 10.0]):
                        exit_code, stdout, stderr = launcher.run(
                            workspace=workspace,
                            session_id="sess1",
                            role="ai_it_topic_runner",
                            invocation=_invocation(),
                            timeout_seconds=1.0,
                        )
        self.assertEqual(exit_code, 1)
        self.assertIn("did not complete within", stderr)


class WiresIntoRunClaudeDigestTest(unittest.TestCase):
    """run_claude_via_kubernetes (claude_digest.py) is the glue between the
    docker-independent invocation builder and this launcher."""

    def test_run_claude_via_kubernetes_builds_invocation_and_delegates_to_launcher(self) -> None:
        from shichimimi_agent.runner.claude_digest import run_claude_via_kubernetes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env_backup = dict(os.environ)
            os.environ["CLAUDE_PROXY_URL"] = "http://claude-proxy:18080"
            os.environ["CLAUDE_PROXY_SESSION_TOKEN"] = "cp-sess-secret"
            os.environ["GIT_PROXY_URL"] = "http://auth-proxy:18081"
            os.environ["GIT_PROXY_SESSION_TOKEN"] = "gp-sess-secret"
            try:
                with mock.patch("shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher.run") as run_mock:
                    run_mock.return_value = (0, "stdout-content", "")
                    result = run_claude_via_kubernetes(
                        workspace=workspace,
                        session_id="sess1",
                        role="ai_it_topic_runner",
                        prompt="do the digest",
                        options=ClaudeDigestOptions(),
                    )
            finally:
                os.environ.clear()
                os.environ.update(env_backup)

        self.assertEqual(result, (0, "stdout-content", ""))
        run_mock.assert_called_once()
        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs["session_id"], "sess1")
        self.assertEqual(kwargs["invocation"].prompt, "do the digest")
        self.assertEqual(kwargs["timeout_seconds"], ClaudeDigestOptions().timeout_seconds)

    def test_run_claude_via_kubernetes_does_not_crash_against_real_launcher_run_signature(self) -> None:
        """Regression: run_claude_via_kubernetes must call
        KubernetesClaudeLauncher.run() with a call signature the real method
        actually accepts. Only the HTTP layer (_api_request) is mocked here
        -- unlike the test above, KubernetesClaudeLauncher.run itself is
        *not* mocked out, so a keyword-argument mismatch between the two
        (e.g. a missing required ``role`` kwarg) surfaces as a TypeError
        instead of being hidden by the mock silently accepting any kwargs."""
        from shichimimi_agent.runner.claude_digest import run_claude_via_kubernetes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            env_backup = dict(os.environ)
            os.environ["CLAUDE_PROXY_URL"] = "http://claude-proxy:18080"
            os.environ["CLAUDE_PROXY_SESSION_TOKEN"] = "cp-sess-secret"
            os.environ["GIT_PROXY_URL"] = "http://auth-proxy:18081"
            os.environ["GIT_PROXY_SESSION_TOKEN"] = "gp-sess-secret"
            try:
                with mock.patch(
                    "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._api_request",
                    return_value={"status": {"succeeded": 1}},
                ), mock.patch(
                    "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._namespace",
                    return_value="test-ns",
                ):
                    result = run_claude_via_kubernetes(
                        workspace=workspace,
                        session_id="sess1",
                        role="ai_it_topic_runner",
                        prompt="do the digest",
                        options=ClaudeDigestOptions(),
                    )
            finally:
                os.environ.clear()
                os.environ.update(env_backup)

        self.assertEqual(result, (0, "", ""))


class RunnerBackendSelectionTest(unittest.TestCase):
    """RUNNER_BACKEND=kubernetes must route run_claude_digest /
    run_invest_digest through the k8s launcher instead of `docker run` +
    subprocess.run -- and must never do both."""

    def setUp(self) -> None:
        from shichimimi_agent.config import load_config
        from shichimimi_agent.db import Repository, migrate
        from shichimimi_agent.proxies.auth_proxy_client import AuthProxyClient
        from shichimimi_agent.security.policy_engine import PolicyEngine
        from shichimimi_agent.sessions.workspace import create_workspace

        self.root = Path(__file__).resolve().parents[1]
        self.config_obj = load_config(self.root)
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "app.sqlite"
        migrate(db_path)
        self.repository = Repository(db_path)
        self.auth_client = AuthProxyClient(local_fallback_engine=PolicyEngine(self.config_obj.policy))

        self._session_id_for_cleanup = "test-k8s-backend-select-" + next(tempfile._get_candidate_names())
        self.workspace_dir = create_workspace(self.root, self._session_id_for_cleanup)

        self._env_backup = dict(os.environ)
        os.environ["RUNNER_BACKEND"] = "kubernetes"
        os.environ["X_MCP_URL"] = "http://auth-proxy:18081"
        os.environ["X_MCP_SESSION_TOKEN"] = "static-admin-token"
        os.environ["CLAUDE_PROXY_URL"] = "http://claude-proxy:18080"
        os.environ["CLAUDE_PROXY_SESSION_TOKEN"] = "cp_sess_dev_secret"
        os.environ["GIT_PROXY_URL"] = "http://auth-proxy:18081"
        os.environ["GIT_PROXY_SESSION_TOKEN"] = "gp_sess_dev_secret"

    def tearDown(self) -> None:
        import shutil

        self._tmpdir.cleanup()
        shutil.rmtree(self.root / ".sessions" / self._session_id_for_cleanup, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_run_claude_digest_uses_kubernetes_launcher_not_subprocess_when_backend_is_kubernetes(self) -> None:
        from shichimimi_agent.runner.claude_digest import run_claude_digest
        from shichimimi_agent.runner.mcp_session import IssuedSession

        with mock.patch("shichimimi_agent.runner.claude_digest.issue_session") as issue_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest.run_claude_via_kubernetes") as k8s_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest.subprocess.run") as subprocess_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest._verify_published") as verify_mock:
            issue_mock.return_value = IssuedSession(token="minted-sess-tok", ttl_seconds=2100)
            k8s_mock.return_value = (0, '{"ok": true}', "")
            verify_mock.return_value = (True, "a" * 40)

            result = run_claude_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess1",
                task_id="task1",
                workspace=self.workspace_dir,
                job={"inputs": {"query_set": "ai_it_watch"}},
                options=ClaudeDigestOptions(),
                auth_client=self.auth_client,
            )

        k8s_mock.assert_called_once()
        subprocess_mock.assert_not_called()
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.verified)
        self.assertEqual(result.stdout, '{"ok": true}')

    def test_run_claude_digest_uses_docker_subprocess_when_backend_unset(self) -> None:
        import subprocess

        from shichimimi_agent.runner.claude_digest import run_claude_digest
        from shichimimi_agent.runner.mcp_session import IssuedSession

        del os.environ["RUNNER_BACKEND"]

        with mock.patch("shichimimi_agent.runner.claude_digest.issue_session") as issue_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest.run_claude_via_kubernetes") as k8s_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest.subprocess.run") as subprocess_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest._verify_published") as verify_mock:
            issue_mock.return_value = IssuedSession(token="minted-sess-tok", ttl_seconds=2100)
            subprocess_mock.return_value = subprocess.CompletedProcess(args=["docker"], returncode=0, stdout="{}", stderr="")
            verify_mock.return_value = (True, "a" * 40)

            run_claude_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess2",
                task_id="task2",
                workspace=self.workspace_dir,
                job={"inputs": {"query_set": "ai_it_watch"}},
                options=ClaudeDigestOptions(),
                auth_client=self.auth_client,
            )

        subprocess_mock.assert_called_once()
        k8s_mock.assert_not_called()

    def test_run_invest_digest_uses_kubernetes_launcher_not_subprocess_when_backend_is_kubernetes(self) -> None:
        from shichimimi_agent.proxies.slack_notify_client import SlackNotifyClient
        from shichimimi_agent.runner.invest_digest import InvestDigestOptions, run_invest_digest
        from shichimimi_agent.runner.mcp_session import IssuedSession

        os.environ["SLACK_NOTIFY_URL"] = "http://auth-proxy:18081"
        os.environ["SLACK_NOTIFY_SESSION_TOKEN"] = "static-admin-token"

        class FakeSlackClient(SlackNotifyClient):
            def __init__(self) -> None:
                pass

            def notify(self, text: str) -> int:
                return 1

        def fake_k8s_run(**kwargs):
            (self.workspace_dir / "digest.md").write_text("*日経平均* 観測整理", encoding="utf-8")
            return 0, "", ""

        with mock.patch("shichimimi_agent.runner.invest_digest.issue_session") as issue_mock, \
             mock.patch("shichimimi_agent.runner.invest_digest.run_claude_via_kubernetes", side_effect=fake_k8s_run) as k8s_mock, \
             mock.patch("shichimimi_agent.runner.invest_digest.subprocess.run") as subprocess_mock:
            issue_mock.return_value = IssuedSession(token="minted-sess-tok", ttl_seconds=2100)

            result = run_invest_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess3",
                task_id="task3",
                workspace=self.workspace_dir,
                job={"role": "investment_signal_runner", "inputs": {"query_set": "invest_watch"}},
                options=InvestDigestOptions(),
                auth_client=self.auth_client,
                slack_client=FakeSlackClient(),
            )

        k8s_mock.assert_called_once()
        subprocess_mock.assert_not_called()
        self.assertTrue(result.published)


class RunnerBackendSelectionAgainstRealLauncherTest(unittest.TestCase):
    """Same RUNNER_BACKEND=kubernetes dispatch as RunnerBackendSelectionTest,
    but without mocking run_claude_via_kubernetes (the glue function) or
    KubernetesClaudeLauncher.run itself -- only the k8s HTTP layer
    (_api_request/_namespace) is mocked, so a kwarg mismatch anywhere along
    run_claude_digest/run_invest_digest -> run_claude_via_kubernetes ->
    KubernetesClaudeLauncher.run surfaces as a real TypeError instead of
    being absorbed by a permissive mock (the exact gap that let the missing
    `role=role` regression through undetected)."""

    def setUp(self) -> None:
        from shichimimi_agent.config import load_config
        from shichimimi_agent.db import Repository, migrate
        from shichimimi_agent.proxies.auth_proxy_client import AuthProxyClient
        from shichimimi_agent.security.policy_engine import PolicyEngine
        from shichimimi_agent.sessions.workspace import create_workspace

        self.root = Path(__file__).resolve().parents[1]
        self.config_obj = load_config(self.root)
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "app.sqlite"
        migrate(db_path)
        self.repository = Repository(db_path)
        self.auth_client = AuthProxyClient(local_fallback_engine=PolicyEngine(self.config_obj.policy))

        self._session_id_for_cleanup = "test-k8s-real-launcher-" + next(tempfile._get_candidate_names())
        self.workspace_dir = create_workspace(self.root, self._session_id_for_cleanup)

        self._env_backup = dict(os.environ)
        os.environ["RUNNER_BACKEND"] = "kubernetes"
        os.environ["X_MCP_URL"] = "http://auth-proxy:18081"
        os.environ["X_MCP_SESSION_TOKEN"] = "static-admin-token"
        os.environ["CLAUDE_PROXY_URL"] = "http://claude-proxy:18080"
        os.environ["CLAUDE_PROXY_SESSION_TOKEN"] = "cp_sess_dev_secret"
        os.environ["GIT_PROXY_URL"] = "http://auth-proxy:18081"
        os.environ["GIT_PROXY_SESSION_TOKEN"] = "gp_sess_dev_secret"

        self._api_request_patcher = mock.patch(
            "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._api_request",
            return_value={"status": {"succeeded": 1}},
        )
        self._namespace_patcher = mock.patch(
            "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._namespace",
            return_value="test-ns",
        )
        self._api_request_patcher.start()
        self._namespace_patcher.start()

    def tearDown(self) -> None:
        import shutil

        self._api_request_patcher.stop()
        self._namespace_patcher.stop()
        self._tmpdir.cleanup()
        shutil.rmtree(self.root / ".sessions" / self._session_id_for_cleanup, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_run_claude_digest_reaches_real_launcher_without_typeerror(self) -> None:
        from shichimimi_agent.runner.claude_digest import run_claude_digest
        from shichimimi_agent.runner.mcp_session import IssuedSession

        (self.workspace_dir / STDOUT_FILENAME).write_text('{"ok": true}', encoding="utf-8")

        with mock.patch("shichimimi_agent.runner.claude_digest.issue_session") as issue_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest._verify_published") as verify_mock:
            issue_mock.return_value = IssuedSession(token="minted-sess-tok", ttl_seconds=2100)
            verify_mock.return_value = (True, "a" * 40)

            result = run_claude_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess1",
                task_id="task1",
                workspace=self.workspace_dir,
                job={"inputs": {"query_set": "ai_it_watch"}},
                options=ClaudeDigestOptions(),
                auth_client=self.auth_client,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.verified)
        self.assertEqual(result.stdout, '{"ok": true}')

    def test_run_invest_digest_reaches_real_launcher_without_typeerror(self) -> None:
        from shichimimi_agent.proxies.slack_notify_client import SlackNotifyClient
        from shichimimi_agent.runner.invest_digest import InvestDigestOptions, run_invest_digest
        from shichimimi_agent.runner.mcp_session import IssuedSession

        os.environ["SLACK_NOTIFY_URL"] = "http://auth-proxy:18081"
        os.environ["SLACK_NOTIFY_SESSION_TOKEN"] = "static-admin-token"

        class FakeSlackClient(SlackNotifyClient):
            def __init__(self) -> None:
                pass

            def notify(self, text: str) -> int:
                return 1

        # The real KubernetesClaudeLauncher.run() reads digest.md back from
        # the workspace only via the caller's own filesystem access after
        # the Job "completes" (the Job container itself never actually
        # runs here) -- write it up front so _read_digest finds it.
        (self.workspace_dir / "digest.md").write_text("*日経平均* 観測整理", encoding="utf-8")

        with mock.patch("shichimimi_agent.runner.invest_digest.issue_session") as issue_mock:
            issue_mock.return_value = IssuedSession(token="minted-sess-tok", ttl_seconds=2100)

            result = run_invest_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess3",
                task_id="task3",
                workspace=self.workspace_dir,
                job={"role": "investment_signal_runner", "inputs": {"query_set": "invest_watch"}},
                options=InvestDigestOptions(),
                auth_client=self.auth_client,
                slack_client=FakeSlackClient(),
            )

        self.assertTrue(result.published)


class ErrorExcerptUtilityTest(unittest.TestCase):
    def test_none_and_empty_and_whitespace_only_yield_none(self) -> None:
        self.assertIsNone(error_excerpt(None))
        self.assertIsNone(error_excerpt(""))
        self.assertIsNone(error_excerpt("   \n  "))

    def test_short_text_passed_through_stripped(self) -> None:
        self.assertEqual(error_excerpt("  boom: BackoffLimitExceeded  "), "boom: BackoffLimitExceeded")

    def test_long_text_truncated_with_marker(self) -> None:
        from shichimimi_agent.runner.claude_digest import MAX_ERROR_METADATA_CHARS

        long_text = "x" * (MAX_ERROR_METADATA_CHARS + 500)
        result = error_excerpt(long_text)
        self.assertTrue(result.endswith("... (truncated)"))
        self.assertLessEqual(len(result), MAX_ERROR_METADATA_CHARS + len("... (truncated)"))


class ErrorMetadataPropagationTest(unittest.TestCase):
    """Tech-lead review (Issue #31): the k8s Job's failure condition
    reason/message (surfaced as `stderr` by KubernetesClaudeLauncher.run(),
    see RunFailurePathTest) must reach repository.record_document's
    metadata, since ttlSecondsAfterFinished=600 reaps the Job/Pod (and its
    events) soon after -- metadata is the only durable post-mortem trail."""

    def setUp(self) -> None:
        from shichimimi_agent.config import load_config
        from shichimimi_agent.db import Repository, migrate
        from shichimimi_agent.proxies.auth_proxy_client import AuthProxyClient
        from shichimimi_agent.security.policy_engine import PolicyEngine
        from shichimimi_agent.sessions.workspace import create_workspace

        self.root = Path(__file__).resolve().parents[1]
        self.config_obj = load_config(self.root)
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "app.sqlite"
        migrate(self.db_path)
        self.repository = Repository(self.db_path)
        self.auth_client = AuthProxyClient(local_fallback_engine=PolicyEngine(self.config_obj.policy))

        self._session_id_for_cleanup = "test-error-metadata-" + next(tempfile._get_candidate_names())
        self.workspace_dir = create_workspace(self.root, self._session_id_for_cleanup)

        self._env_backup = dict(os.environ)
        os.environ["RUNNER_BACKEND"] = "kubernetes"
        os.environ["X_MCP_URL"] = "http://auth-proxy:18081"
        os.environ["X_MCP_SESSION_TOKEN"] = "static-admin-token"
        os.environ["CLAUDE_PROXY_URL"] = "http://claude-proxy:18080"
        os.environ["CLAUDE_PROXY_SESSION_TOKEN"] = "cp_sess_dev_secret"
        os.environ["GIT_PROXY_URL"] = "http://auth-proxy:18081"
        os.environ["GIT_PROXY_SESSION_TOKEN"] = "gp_sess_dev_secret"

    def tearDown(self) -> None:
        import shutil

        self._tmpdir.cleanup()
        shutil.rmtree(self.root / ".sessions" / self._session_id_for_cleanup, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._env_backup)

    def _fetch_documents(self) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute("SELECT * FROM documents").fetchall())
        finally:
            conn.close()

    def test_run_claude_digest_records_job_failure_condition_in_metadata(self) -> None:
        from shichimimi_agent.runner.claude_digest import run_claude_digest
        from shichimimi_agent.runner.mcp_session import IssuedSession

        with mock.patch("shichimimi_agent.runner.claude_digest.issue_session") as issue_mock, \
             mock.patch(
                 "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._namespace",
                 return_value="test-ns",
             ), \
             mock.patch(
                 "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._api_request",
                 side_effect=[
                     {},  # POST create job
                     {
                         "status": {
                             "failed": 1,
                             "conditions": [{"type": "Failed", "reason": "DeadlineExceeded", "message": "Job was active longer than specified deadline"}],
                         }
                     },
                 ],
             ):
            issue_mock.return_value = IssuedSession(token="minted-sess-tok", ttl_seconds=2100)

            result = run_claude_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess1",
                task_id="task1",
                workspace=self.workspace_dir,
                job={"inputs": {"query_set": "ai_it_watch"}},
                options=ClaudeDigestOptions(),
                auth_client=self.auth_client,
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(result.verified)

        rows = self._fetch_documents()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "failed")
        metadata = json.loads(rows[0]["metadata_json"])
        self.assertIsNotNone(metadata["error"])
        self.assertIn("DeadlineExceeded", metadata["error"])
        self.assertIn("Job was active longer than specified deadline", metadata["error"])

    def test_run_claude_digest_success_leaves_error_metadata_none(self) -> None:
        from shichimimi_agent.runner.claude_digest import run_claude_digest
        from shichimimi_agent.runner.mcp_session import IssuedSession

        (self.workspace_dir / STDOUT_FILENAME).write_text('{"ok": true}', encoding="utf-8")

        with mock.patch("shichimimi_agent.runner.claude_digest.issue_session") as issue_mock, \
             mock.patch("shichimimi_agent.runner.claude_digest._verify_published") as verify_mock, \
             mock.patch(
                 "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._namespace",
                 return_value="test-ns",
             ), \
             mock.patch(
                 "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._api_request",
                 return_value={"status": {"succeeded": 1}},
             ):
            issue_mock.return_value = IssuedSession(token="minted-sess-tok", ttl_seconds=2100)
            verify_mock.return_value = (True, "a" * 40)

            run_claude_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess2",
                task_id="task2",
                workspace=self.workspace_dir,
                job={"inputs": {"query_set": "ai_it_watch"}},
                options=ClaudeDigestOptions(),
                auth_client=self.auth_client,
            )

        rows = self._fetch_documents()
        self.assertEqual(len(rows), 1)
        metadata = json.loads(rows[0]["metadata_json"])
        self.assertIsNone(metadata["error"])

    def test_run_invest_digest_records_job_failure_condition_in_metadata(self) -> None:
        from shichimimi_agent.runner.invest_digest import InvestDigestOptions, run_invest_digest
        from shichimimi_agent.runner.mcp_session import IssuedSession

        with mock.patch("shichimimi_agent.runner.invest_digest.issue_session") as issue_mock, \
             mock.patch(
                 "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._namespace",
                 return_value="test-ns",
             ), \
             mock.patch(
                 "shichimimi_agent.runner.k8s_claude_launcher.KubernetesClaudeLauncher._api_request",
                 side_effect=[
                     {},
                     {
                         "status": {
                             "failed": 1,
                             "conditions": [{"type": "Failed", "reason": "BackoffLimitExceeded", "message": "boom"}],
                         }
                     },
                 ],
             ):
            issue_mock.return_value = IssuedSession(token="minted-sess-tok", ttl_seconds=2100)

            result = run_invest_digest(
                config=self.config_obj,
                repository=self.repository,
                session_id="sess3",
                task_id="task3",
                workspace=self.workspace_dir,
                job={"role": "investment_signal_runner", "inputs": {"query_set": "invest_watch"}},
                options=InvestDigestOptions(),
                auth_client=self.auth_client,
            )

        self.assertFalse(result.published)

        rows = self._fetch_documents()
        self.assertEqual(len(rows), 1)
        metadata = json.loads(rows[0]["metadata_json"])
        self.assertIsNotNone(metadata["error"])
        self.assertIn("BackoffLimitExceeded", metadata["error"])
        self.assertIn("boom", metadata["error"])


class DockerCommandEnvCopySafetyTest(unittest.TestCase):
    """Tech-lead review (Issue #31): build_docker_command must never mutate
    the ClaudeInvocation it (or a caller) built -- it should copy env
    before layering on RUNNER_NETWORK/RUNNER_EGRESS_PROXY additions,
    matching KubernetesClaudeLauncher._build_env's copy-before-mutate."""

    def test_build_docker_command_does_not_mutate_passed_invocation_env(self) -> None:
        from shichimimi_agent.runner import claude_digest as claude_digest_module
        from shichimimi_agent.runner.claude_digest import build_docker_command

        original_env = {"SESSION_ID": "sess1", "ANTHROPIC_MODEL": "claude-sonnet-5"}
        invocation = ClaudeInvocation(prompt="p", allowed_tools="Read,Write", extra_args=[], env=original_env)

        env_backup = dict(os.environ)
        os.environ["RUNNER_NETWORK"] = "7mimi-internal"
        os.environ["RUNNER_EGRESS_PROXY"] = "http://egress-proxy:18082"
        try:
            with mock.patch.object(claude_digest_module, "build_claude_invocation", return_value=invocation):
                with tempfile.TemporaryDirectory() as tmp:
                    cmd = build_docker_command(
                        workspace=Path(tmp),
                        session_id="sess1",
                        role="ai_it_topic_runner",
                        prompt="p",
                        options=ClaudeDigestOptions(),
                    )
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

        # The proxy env vars did make it into the docker command...
        joined = " ".join(cmd)
        self.assertIn("HTTPS_PROXY=http://egress-proxy:18082", joined)
        # ...but the original ClaudeInvocation.env dict we passed in (via the
        # mocked build_claude_invocation) must be untouched.
        self.assertEqual(original_env, {"SESSION_ID": "sess1", "ANTHROPIC_MODEL": "claude-sonnet-5"})
        self.assertNotIn("HTTPS_PROXY", original_env)
        self.assertNotIn("HTTP_PROXY", original_env)
        self.assertNotIn("NO_PROXY", original_env)

    def test_invocation_env_is_reusable_across_multiple_build_docker_command_calls(self) -> None:
        """A regression guard on the mutation bug directly: build the SAME
        ClaudeInvocation-shaped env dict content twice through the real
        (unmocked) build_claude_invocation, once with RUNNER_NETWORK set and
        once without -- the second call must not see leftover state from the
        first (which it would if some shared mutable default or module-level
        dict were involved)."""
        from shichimimi_agent.runner.claude_digest import build_docker_command

        env_backup = dict(os.environ)
        os.environ["CLAUDE_PROXY_URL"] = "http://claude-proxy:18080"
        os.environ["CLAUDE_PROXY_SESSION_TOKEN"] = "cp-sess-secret"
        os.environ["GIT_PROXY_URL"] = "http://auth-proxy:18081"
        os.environ["GIT_PROXY_SESSION_TOKEN"] = "gp-sess-secret"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["RUNNER_NETWORK"] = "7mimi-internal"
                os.environ["RUNNER_EGRESS_PROXY"] = "http://egress-proxy:18082"
                cmd_with_proxy = build_docker_command(
                    workspace=Path(tmp), session_id="sess1", role="ai_it_topic_runner", prompt="p",
                    options=ClaudeDigestOptions(),
                )

                os.environ.pop("RUNNER_NETWORK", None)
                os.environ.pop("RUNNER_EGRESS_PROXY", None)
                cmd_without_proxy = build_docker_command(
                    workspace=Path(tmp), session_id="sess2", role="ai_it_topic_runner", prompt="p",
                    options=ClaudeDigestOptions(),
                )
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

        self.assertIn("HTTPS_PROXY=http://egress-proxy:18082", " ".join(cmd_with_proxy))
        self.assertNotIn("HTTPS_PROXY", " ".join(cmd_without_proxy))
        self.assertNotIn("HTTP_PROXY", " ".join(cmd_without_proxy))
        self.assertNotIn("NO_PROXY", " ".join(cmd_without_proxy))


if __name__ == "__main__":
    unittest.main()
