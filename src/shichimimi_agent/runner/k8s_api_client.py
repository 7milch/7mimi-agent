"""Shared in-cluster k8s API client plumbing (Issue #29 / Issue #31).

Extracted out of kubernetes_runner.py so that both `KubernetesRunnerBackend`
(runner-execute Jobs) and `KubernetesClaudeLauncher` (k8s_claude_launcher.py,
Issue #31 -- claude CLI Jobs replacing the nested `docker run`) share the
exact same stdlib-`urllib` in-cluster REST client and Job-completion polling
logic instead of two copies drifting apart.

Talks to the API server with stdlib `urllib` only (no `kubernetes` package,
per repo convention). The ServiceAccount token is re-read from disk on every
request (BoundServiceAccountToken rotation, default since k8s 1.21+), so
callers must not cache it.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

# In-cluster ServiceAccount projection (k3s / any k8s).
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
DEFAULT_API_SERVER = "https://kubernetes.default.svc"


class _K8sApiClientOptions(Protocol):
    api_server: str
    ca_cert_path: Path
    token_path: Path
    namespace_path: Path
    namespace: str | None
    request_timeout_seconds: float
    poll_interval_seconds: float
    timeout_seconds: float


class KubernetesApiClientMixin:
    """Mixin providing in-cluster k8s REST API access + Job completion
    polling. Subclasses must set ``self.options`` to an object exposing the
    fields in ``_K8sApiClientOptions`` and call ``self._init_k8s_api_client()``
    before first use (normally from ``__init__``).
    """

    options: _K8sApiClientOptions

    def _init_k8s_api_client(self) -> None:
        self._namespace_cache: str | None = None

    # -- k8s API plumbing, kept as thin wrappers so tests can mock just this layer --

    def _read_token(self) -> str:
        return self.options.token_path.read_text(encoding="utf-8").strip()

    def _namespace(self) -> str:
        if self.options.namespace:
            return self.options.namespace
        if self._namespace_cache is None:
            self._namespace_cache = self.options.namespace_path.read_text(encoding="utf-8").strip()
        return self._namespace_cache

    def _ssl_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=str(self.options.ca_cert_path))

    def _api_request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.options.api_server}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._read_token()}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, context=self._ssl_context(), timeout=self.options.request_timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"kubernetes API {method} {path} failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"kubernetes API {method} {path} unreachable: {exc.reason}") from exc
        if not raw:
            return {}
        return json.loads(raw)

    # -- Job completion polling --

    def _wait_for_completion(self, *, namespace: str, job_name: str, timeout_seconds: float | None = None) -> None:
        """Poll ``.status`` (no watch) until the Job succeeds or fails.

        ``timeout_seconds`` overrides ``self.options.timeout_seconds`` for
        this call (callers whose per-job timeout varies at call time, e.g.
        KubernetesClaudeLauncher, pass it explicitly; callers with a static
        configured timeout, e.g. KubernetesRunnerBackend, omit it).
        """
        effective_timeout = self.options.timeout_seconds if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + effective_timeout
        while True:
            job = self._api_request("GET", f"/apis/batch/v1/namespaces/{namespace}/jobs/{job_name}")
            status = job.get("status") or {}
            if int(status.get("succeeded") or 0) >= 1:
                return
            if int(status.get("failed") or 0) >= 1:
                conditions = status.get("conditions") or []
                failed_condition = next((c for c in conditions if c.get("type") == "Failed"), None)
                detail = self._failed_condition_detail(failed_condition)
                raise RuntimeError(f"Job {namespace}/{job_name} failed: {detail}")
            if time.monotonic() > deadline:
                raise RuntimeError(f"Job {namespace}/{job_name} did not complete within {effective_timeout}s")
            time.sleep(self.options.poll_interval_seconds)

    @staticmethod
    def _failed_condition_detail(condition: dict[str, Any] | None) -> str:
        # Tech-lead review (Issue #31): surface both `reason` (a short,
        # stable machine code like DeadlineExceeded/BackoffLimitExceeded)
        # and `message` (the human-readable detail) when the Job's Failed
        # condition carries both -- dropping either loses information a
        # caller may need for post-mortem once ttlSecondsAfterFinished reaps
        # the Job/Pod and its events.
        if not condition:
            return "Job reported failed status"
        reason = condition.get("reason")
        message = condition.get("message")
        if reason and message:
            return f"{reason}: {message}"
        return message or reason or "Job reported failed status"
