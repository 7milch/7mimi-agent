"""Issue #31: in-cluster claude CLI launcher (k8s Job), replacing the nested
`docker run` (ADR-021/024) that k3s scheduler Pods cannot execute (no
docker.sock in-cluster).

Reuses the shared in-cluster k8s REST client + Job-completion polling from
`k8s_api_client.py` (the same plumbing `KubernetesRunnerBackend` uses,
kubernetes_runner.py) and the docker-independent claude CLI invocation
(`ClaudeInvocation` / `build_claude_invocation`, claude_digest.py).

Job hardening mirrors `KubernetesRunnerBackend._job_manifest` exactly: the
runner label (NetworkPolicy selector), UID/GID 10001, no ServiceAccount
token automount, restricted securityContext, node pin, PVC mount,
imagePullSecret, `backoffLimit: 0` / `restartPolicy: Never`, ttl, and no
ArgoCD `app.kubernetes.io/instance` tracking label.

Workspace: the session workspace directory the scheduler already wrote
`.mcp.json` (and, for ai-it digest, will later clone-verify) into is mounted
at `/workspace` via a PVC subPath -- the same physical files a `docker run
-v <workspace>:/workspace` bind-mount would expose, just accessed through
the shared PVC instead. stdout/stderr are captured to files under that same
mount (not Pod logs, which are not a stable machine-readable channel and are
subject to containerd log rotation/mixing) and read back by the scheduler
process directly from its own (already-PVC-backed) `workspace` Path.

Command construction avoids shell injection from the (orchestrator/config
composed, but still free-form) prompt and allowed-tools string: both are
passed to the container via env vars and referenced in the `sh -c` script
only as `"$VAR"` -- double-quoted parameter expansion, which performs no
word-splitting, globbing, or further shell parsing of the substituted text
(unlike `eval`), so no value placed in CLAUDE_PROMPT/CLAUDE_ALLOWED_TOOLS can
break out of its argument position or run another command. Every other CLI
flag (mcp-config path, --max-turns, --output-format) is a static/derived
literal with no untrusted content, so those are shell-quoted with `shlex.quote`
and inlined directly.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .k8s_api_client import DEFAULT_API_SERVER, SA_DIR, KubernetesApiClientMixin

if TYPE_CHECKING:
    from .claude_digest import ClaudeInvocation

STDOUT_FILENAME = ".claude-stdout.json"
STDERR_FILENAME = ".claude-stderr.log"

# NO_PROXY when RUNNER_EGRESS_PROXY is set: excludes the boundary services
# themselves (addressed by service name) so proxy/relay traffic doesn't loop
# back through egress-proxy (ADR-025), matching the docker launcher's
# RUNNER_NETWORK branch in claude_digest.build_docker_command.
_NO_PROXY = "claude-proxy,auth-proxy,egress-proxy,localhost,127.0.0.1"


@dataclass(frozen=True)
class KubernetesClaudeLauncherOptions:
    """Wiring for the claude-CLI Job (Issue #31). Defaults mirror
    KubernetesRunnerOptions (kubernetes_runner.py) and read from the same
    env vars set on the scheduler Deployment (deploy/k8s/scheduler.yaml), so
    a single RUNNER_IMAGE/RUNNER_PVC_NAME/RUNNER_NODE_HOSTNAME/RUNNER_UID/
    RUNNER_GID set pins both the runner-execute Job and this one.
    """

    image: str = field(default_factory=lambda: os.environ.get("RUNNER_IMAGE", "ghcr.io/7milch/7mimi-agent-agent-runner:latest"))
    namespace: str | None = None
    pvc_name: str = field(default_factory=lambda: os.environ.get("RUNNER_PVC_NAME", "7mimi-agent-data"))
    node_hostname: str = field(default_factory=lambda: os.environ.get("RUNNER_NODE_HOSTNAME", "john-cooper-works"))
    image_pull_secret: str = field(default_factory=lambda: os.environ.get("RUNNER_IMAGE_PULL_SECRET", "ghcr-pull-secret"))
    run_as_user: int = field(default_factory=lambda: int(os.environ.get("RUNNER_UID", "10001")))
    run_as_group: int = field(default_factory=lambda: int(os.environ.get("RUNNER_GID", "10001")))
    memory_limit: str = "2Gi"
    api_server: str = DEFAULT_API_SERVER
    ca_cert_path: Path = SA_DIR / "ca.crt"
    token_path: Path = SA_DIR / "token"
    namespace_path: Path = SA_DIR / "namespace"
    poll_interval_seconds: float = 5.0
    # Static fallback only -- KubernetesClaudeLauncher.run() always passes an
    # explicit per-call timeout_seconds (from ClaudeDigestOptions /
    # InvestDigestOptions), which the shared _wait_for_completion mixin uses
    # in preference to this field.
    timeout_seconds: float = 1200.0
    # Tech-lead review (Issue #31): the scheduler-side poll loop must outlive
    # the Job's own `activeDeadlineSeconds` by a margin, or the poll loop's
    # own timeout can fire first and race k8s's clean DeadlineExceeded Job
    # condition -- losing that (more informative) failure reason in favor of
    # our own generic "did not complete within Ns" message. activeDeadlineSeconds
    # itself (_job_manifest) is left unbuffered -- it's the real, enforced
    # ceiling; only *our own* poll timeout gets the margin.
    poll_timeout_buffer_seconds: float = 60.0
    ttl_seconds_after_finished: int = 600
    runner_label: str = "7mimi-agent-runner"
    job_name_prefix: str = "7mimi-claude-digest"
    request_timeout_seconds: float = 30.0


class KubernetesClaudeLauncher(KubernetesApiClientMixin):
    """Runs a claude CLI invocation as a batch/v1 Job on the in-cluster k3s
    API, in place of `docker run ... claude -p ...` (Issue #31).
    """

    def __init__(self, *, options: KubernetesClaudeLauncherOptions | None = None) -> None:
        self.options = options or KubernetesClaudeLauncherOptions()
        self._init_k8s_api_client()

    def _job_name(self, session_id: str) -> str:
        # Job/Pod names must be lowercase RFC1123 labels; session ids are
        # already lowercase alnum + underscores (util/ids.py).
        suffix = session_id.replace("_", "-").lower()
        return f"{self.options.job_name_prefix}-{suffix}"[:63].rstrip("-")

    def _build_command(self, invocation: "ClaudeInvocation") -> list[str]:
        static_args = " ".join(shlex.quote(arg) for arg in invocation.extra_args)
        script = (
            'claude -p "$CLAUDE_PROMPT" --allowedTools "$CLAUDE_ALLOWED_TOOLS" '
            f"{static_args} "
            f"> /workspace/{STDOUT_FILENAME} 2> /workspace/{STDERR_FILENAME}"
        )
        return ["sh", "-c", script]

    def _build_env(self, invocation: "ClaudeInvocation") -> list[dict[str, str]]:
        env = dict(invocation.env)
        # Prompt / allowedTools carry orchestrator/config-composed free-form
        # text -- passed via env + "$VAR" (see module docstring), never
        # interpolated into the sh -c script string itself.
        env["CLAUDE_PROMPT"] = invocation.prompt
        env["CLAUDE_ALLOWED_TOOLS"] = invocation.allowed_tools

        egress_proxy_url = os.environ.get("RUNNER_EGRESS_PROXY")
        if egress_proxy_url:
            env["HTTPS_PROXY"] = egress_proxy_url
            env["HTTP_PROXY"] = egress_proxy_url
            env["NO_PROXY"] = _NO_PROXY

        return [{"name": key, "value": value} for key, value in env.items()]

    def _job_manifest(
        self,
        *,
        session_id: str,
        role: str,
        invocation: "ClaudeInvocation",
        timeout_seconds: float,
    ) -> dict[str, Any]:
        job_labels = {
            "app.kubernetes.io/name": self.options.runner_label,
            "app.kubernetes.io/part-of": "7mimi-agent",
        }
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": self._job_name(session_id),
                "namespace": self._namespace(),
                # No app.kubernetes.io/instance label: these Jobs are
                # scheduler-owned, ephemeral, per-run resources and must
                # stay outside ArgoCD's tracked-resource set, matching
                # KubernetesRunnerBackend._job_manifest.
                "labels": {**job_labels, "shichimimi.io/session-id": session_id, "shichimimi.io/role": role},
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": int(timeout_seconds),
                "ttlSecondsAfterFinished": self.options.ttl_seconds_after_finished,
                "template": {
                    "metadata": {"labels": job_labels},
                    "spec": {
                        "restartPolicy": "Never",
                        "nodeSelector": {"kubernetes.io/hostname": self.options.node_hostname},
                        "imagePullSecrets": [{"name": self.options.image_pull_secret}],
                        # This Job never calls the k8s API -- defense in
                        # depth against a compromised claude CLI process
                        # reaching the API server at all.
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": self.options.run_as_user,
                            "runAsGroup": self.options.run_as_group,
                            "fsGroup": self.options.run_as_group,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "claude-digest",
                                "image": self.options.image,
                                "command": self._build_command(invocation),
                                "workingDir": "/workspace",
                                "env": self._build_env(invocation),
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "limits": {"memory": self.options.memory_limit},
                                    "requests": {"memory": self.options.memory_limit},
                                },
                                "volumeMounts": [
                                    {
                                        "name": "workspace",
                                        "mountPath": "/workspace",
                                        # Only this session's own workspace
                                        # directory is mounted -- never the
                                        # whole PVC/repo -- matching the
                                        # docker launcher's workspace-only
                                        # bind mount invariant.
                                        "subPath": f"sessions/{session_id}/workspace",
                                    }
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "workspace", "persistentVolumeClaim": {"claimName": self.options.pvc_name}},
                        ],
                    },
                },
            },
        }

    def run(
        self,
        *,
        workspace: Path,
        session_id: str,
        role: str,
        invocation: "ClaudeInvocation",
        timeout_seconds: float,
    ) -> tuple[int, str, str]:
        """Create the Job, wait for it to complete, and read stdout/stderr
        back from the shared-PVC workspace. Returns (exit_code, stdout,
        stderr): 0/files-on-success, 1/best-effort-stderr-on-failure -- the
        same shape `subprocess.run` would give the docker launcher, so
        callers (run_claude_digest / run_invest_digest) need no k8s-specific
        branching beyond the choice of launcher itself."""
        manifest = self._job_manifest(
            session_id=session_id, role=role, invocation=invocation, timeout_seconds=timeout_seconds
        )
        namespace = manifest["metadata"]["namespace"]
        job_name = manifest["metadata"]["name"]

        try:
            self._api_request("POST", f"/apis/batch/v1/namespaces/{namespace}/jobs", body=manifest)
            # Buffered vs. the Job's own activeDeadlineSeconds (see the
            # option's docstring) so k8s gets the chance to mark the Job
            # Failed (with its condition reason/message) before we do.
            self._wait_for_completion(
                namespace=namespace,
                job_name=job_name,
                timeout_seconds=timeout_seconds + self.options.poll_timeout_buffer_seconds,
            )
        except RuntimeError as exc:
            stderr = self._read_optional(workspace / STDERR_FILENAME)
            message = str(exc)
            if stderr:
                message = f"{message}; stderr: {stderr}"
            return 1, "", message

        stdout = self._read_optional(workspace / STDOUT_FILENAME) or ""
        stderr = self._read_optional(workspace / STDERR_FILENAME) or ""
        return 0, stdout, stderr

    @staticmethod
    def _read_optional(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None
