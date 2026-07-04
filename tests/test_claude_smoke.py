from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from shichimimi_agent.runner.claude_smoke import ClaudeSmokeOptions, build_docker_command


class ClaudeSmokeCommandTest(unittest.TestCase):
    def build(self) -> list[str]:
        return build_docker_command(
            root=Path("/repo"),
            session_id="sess_x",
            role="ai_it_topic_runner",
            workspace_rel=".sessions/sess_x/workspace",
            prompt="do something small",
            options=ClaudeSmokeOptions(),
        )

    def test_no_provider_credentials_forwarded(self) -> None:
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-real-secret"}):
            cmd = self.build()
        joined = " ".join(cmd)
        self.assertNotIn("sk-ant-real-secret", joined)
        self.assertNotIn("ANTHROPIC_API_KEY", joined)

    def test_points_claude_at_proxy(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_PROXY_URL", None)
            cmd = self.build()
        joined = " ".join(cmd)
        self.assertIn("ANTHROPIC_BASE_URL=http://host.docker.internal:18080", joined)
        self.assertIn("ANTHROPIC_AUTH_TOKEN=cp_sess_dev", joined)
        self.assertIn("X-7mimi-Session-Id: sess_x", joined)
        self.assertIn("X-7mimi-Role: ai_it_topic_runner", joined)

    def test_network_is_explicit_bridge_and_claude_invocation(self) -> None:
        cmd = self.build()
        self.assertIn("--network", cmd)
        self.assertEqual(cmd[cmd.index("--network") + 1], "bridge")
        self.assertIn("claude", cmd)
        self.assertIn("--max-turns", cmd)
        self.assertIn("--allowedTools", cmd)


if __name__ == "__main__":
    unittest.main()
