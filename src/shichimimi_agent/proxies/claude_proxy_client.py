"""Client for the Go claude-proxy service.

claude-proxy owns the Claude provider credential; agent-runner only holds a
session-scoped token. This client will be used by the future Claude runner
wrapper to send /v1/messages traffic through the proxy.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ClaudeProxyClient:
    base_url: str = field(default_factory=lambda: os.environ.get("CLAUDE_PROXY_URL", "http://localhost:18080"))
    session_token: str = field(default_factory=lambda: os.environ.get("CLAUDE_PROXY_SESSION_TOKEN", ""))
    session_id: str = ""
    role: str = ""
    timeout_seconds: float = 600.0

    def healthz(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url.rstrip('/')}/healthz", timeout=5) as resp:
                return resp.status == 200
        except OSError:
            return False

    def create_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a non-streaming /v1/messages request through claude-proxy."""
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}",
                "X-7mimi-Session-Id": self.session_id,
                "X-7mimi-Role": self.role,
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8"))
