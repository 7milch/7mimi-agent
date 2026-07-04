"""Client for the Go auth-proxy service.

The PreToolUse hook calls auth-proxy /v1/tool/authorize for deterministic,
fail-closed tool authorization. In local/dev mode (no auth-proxy reachable or
none configured) it falls back to the local Python PolicyEngine so the
dry-run runner keeps working without Go services.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from sevenmimi_agent.security.policy_engine import PolicyDecision, PolicyEngine


@dataclass(frozen=True)
class AuthProxyClient:
    base_url: str = field(default_factory=lambda: os.environ.get("AUTH_PROXY_URL", ""))
    local_fallback_engine: PolicyEngine | None = None
    timeout_seconds: float = 10.0

    @property
    def remote_enabled(self) -> bool:
        return bool(self.base_url)

    def authorize(
        self,
        *,
        session_id: str,
        task_id: str,
        role: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        if self.remote_enabled:
            try:
                return self._authorize_remote(
                    session_id=session_id, task_id=task_id, role=role, tool_name=tool_name, arguments=arguments or {}
                )
            except Exception as exc:
                # Remote configured but unreachable/broken: fail-closed.
                # Local fallback is only for local/dev mode without AUTH_PROXY_URL.
                return PolicyDecision("block", f"auth-proxy request failed: {exc}")
        if self.local_fallback_engine is not None:
            return self.local_fallback_engine.decide_tool_call(role=role, tool_name=tool_name, arguments=arguments)
        return PolicyDecision("block", "no auth-proxy configured and no local policy engine available")

    def _authorize_remote(
        self, *, session_id: str, task_id: str, role: str, tool_name: str, arguments: dict[str, Any]
    ) -> PolicyDecision:
        payload = {
            "session_id": session_id,
            "task_id": task_id,
            "role": role,
            "tool_name": tool_name,
            "arguments": arguments,
        }
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/v1/tool/authorize",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return PolicyDecision(
            decision=body.get("decision", "block"),
            reason=body.get("reason", "missing reason in auth-proxy response"),
            policy_version=str(body.get("policy_version", "unknown")),
        )
