"""Parity guard (ADR-028): the Go auth-proxy DevEngine (internal/policy/policy.go)
must mirror config/policy.yaml's role_tool_policy for the roles it enforces
at the /mcp boundary (ai_it_topic_runner, investment_signal_runner,
stock_researcher). Fails on drift so a policy.yaml edit that isn't ported to
the Go engine is caught by tests rather than discovered in production.

Tool names use dots (e.g. "x.search_posts_recent", "jq.get_listed_info"),
not slashes, in both config/policy.yaml and the Go DevEngine, so Python's
fnmatch and Go's path.Match glob semantics agree for the patterns used here
(only literal names and trailing "*" wildcards, e.g. "jq.*")."""

from __future__ import annotations

import re
import unittest
from fnmatch import fnmatch
from pathlib import Path

from shichimimi_agent.config import load_config

# Roles enforced by the Go /mcp boundary (ADR-028); other role_tool_policy
# roles (orchestrator, x_collector, document_writer, source_verifier) are
# not exposed via /mcp session tokens today and are out of scope here.
_ROLES_TO_CHECK = ["ai_it_topic_runner", "investment_signal_runner", "stock_researcher"]

# Representative tool names to probe per role: enough to exercise every
# allow/deny pattern relevant to the /mcp tool surface (x.*, jq.*, trading.*,
# slack.*, document.write_markdown) without requiring a full glob-semantics
# engine transplant from the Go source.
_PROBE_TOOLS = [
    "x.search_posts_recent",
    "x.get_posts",
    "x.get_users",
    "x.get_users_by_username",
    "x.create_post",
    "x.like_post",
    "x.repost",
    "x.follow_user",
    "x.send_dm",
    "jq.get_listed_info",
    "jq.get_daily_quotes",
    "jq.get_statements",
    "jquants.get_listed_info",
    "trading.buy",
    "trading.sell",
    "slack.post_digest",
    "document.write_markdown",
]


class PolicyParityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.config = load_config(self.root)
        self.policy_go_source = (
            self.root / "services" / "auth-proxy" / "internal" / "policy" / "policy.go"
        ).read_text(encoding="utf-8")

    def _python_decision(self, role: str, tool: str) -> bool:
        """Mirror the Go DevEngine's Decide semantics: deny wins over allow;
        default deny."""
        role_policy = (self.config.policy.get("role_tool_policy") or {}).get(role) or {}
        for pattern in role_policy.get("deny") or []:
            if fnmatch(tool, pattern):
                return False
        for pattern in role_policy.get("allow") or []:
            if fnmatch(tool, pattern):
                return True
        return False

    def _go_role_block(self, role: str) -> str:
        match = re.search(
            rf'"{re.escape(role)}":\s*\{{(.*?)\n\t\t\}},\n', self.policy_go_source, re.DOTALL
        )
        self.assertIsNotNone(
            match,
            f"role {role!r} not found in Go DevEngine (services/auth-proxy/internal/policy/policy.go); "
            "add it to NewDevEngine to keep parity with config/policy.yaml.",
        )
        return match.group(1)

    def _go_patterns(self, role_block: str, list_name: str) -> list[str]:
        match = re.search(rf"{list_name}:\s*\[\]string\{{(.*?)\}},", role_block, re.DOTALL)
        if not match:
            return []
        return re.findall(r'"([^"]+)"', match.group(1))

    def test_go_devengine_matches_policy_yaml_for_mcp_roles(self) -> None:
        for role in _ROLES_TO_CHECK:
            role_block = self._go_role_block(role)
            go_allow = self._go_patterns(role_block, "Allow")
            go_deny = self._go_patterns(role_block, "Deny")

            for tool in _PROBE_TOOLS:
                python_allowed = self._python_decision(role, tool)

                go_denied = any(fnmatch(tool, p) for p in go_deny)
                go_allowed_flag = any(fnmatch(tool, p) for p in go_allow)
                go_allowed = go_allowed_flag and not go_denied

                self.assertEqual(
                    python_allowed,
                    go_allowed,
                    f"policy drift for role={role!r} tool={tool!r}: "
                    f"config/policy.yaml allows={python_allowed}, Go DevEngine allows={go_allowed}. "
                    "Port the change to services/auth-proxy/internal/policy/policy.go's NewDevEngine.",
                )


if __name__ == "__main__":
    unittest.main()
