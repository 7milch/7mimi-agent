"""Parity guard: every config/policy.yaml redaction_policy.patterns entry must
be ported into services/auth-proxy/internal/xmcp/xmcp.go's
defaultRedactionPatterns. Fails when someone adds a policy.yaml pattern
without porting the Go equivalent, so the Go MCP server's redaction cannot
silently drift from the Python Redactor's."""

from __future__ import annotations

import unittest
from pathlib import Path

from shichimimi_agent.config import load_config

# Maps policy.yaml pattern name -> the exact regex literal expected to appear
# in xmcp.go's defaultRedactionPatterns (as a Go raw string between backticks).
# A 1:1 port is used for every current pattern; if a future pattern needs a Go
# adapted equivalent (RE2 cannot express the same regex), add an explicit
# mapping entry here documenting the difference instead of the raw copy.
_EXPECTED_GO_REGEX = {
    "env_assignment": r"(?i)(api[_-]?key|secret|token|password)\s*=",
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "bearer_token": r"Bearer\s+[A-Za-z0-9._~+/-]+=*",
    "anthropic_key": r"sk-ant-[A-Za-z0-9._-]+",
    "claude_proxy_session_token": r"cp_sess_[A-Za-z0-9._-]+",
}


class RedactionParityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.config = load_config(self.root)
        self.xmcp_source = (
            self.root / "services" / "auth-proxy" / "internal" / "xmcp" / "xmcp.go"
        ).read_text(encoding="utf-8")

    def test_every_policy_pattern_has_a_mapping_entry(self) -> None:
        patterns = self.config.policy.get("redaction_policy", {}).get("patterns") or []
        names = {p["name"] for p in patterns}
        missing = names - set(_EXPECTED_GO_REGEX)
        self.assertFalse(
            missing,
            f"policy.yaml redaction pattern(s) {missing} have no Go port mapping in "
            "tests/test_redaction_parity.py; port them to xmcp.go's "
            "defaultRedactionPatterns and add a mapping entry here.",
        )

    def test_every_mapped_regex_is_present_in_go_source(self) -> None:
        patterns = self.config.policy.get("redaction_policy", {}).get("patterns") or []
        for pattern in patterns:
            name = pattern["name"]
            self.assertIn(
                name,
                _EXPECTED_GO_REGEX,
                f"no Go port mapping registered for policy.yaml pattern '{name}'",
            )
            expected_regex = _EXPECTED_GO_REGEX[name]
            self.assertEqual(
                expected_regex,
                pattern["regex"],
                f"policy.yaml regex for '{name}' changed but the Go port mapping was not "
                "updated; re-port to xmcp.go and refresh _EXPECTED_GO_REGEX.",
            )
            self.assertIn(
                expected_regex,
                self.xmcp_source,
                f"xmcp.go is missing the ported regex for pattern '{name}': {expected_regex!r}",
            )

    def test_no_stale_mapping_entries(self) -> None:
        patterns = self.config.policy.get("redaction_policy", {}).get("patterns") or []
        names = {p["name"] for p in patterns}
        stale = set(_EXPECTED_GO_REGEX) - names
        self.assertFalse(
            stale,
            f"mapping entries {stale} no longer correspond to a policy.yaml pattern; "
            "remove them from tests/test_redaction_parity.py",
        )


if __name__ == "__main__":
    unittest.main()
