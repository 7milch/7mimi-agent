"""Regression guard (Issue #18): no role may ever be authorized to perform an
X *write* operation. X posts are signals, never evidence, and X write
operations are prohibited by policy (see CLAUDE.md / policy.yaml
principles). Parametrized over every role defined in
config/policy.yaml's role_tool_policy so a newly added role is covered
automatically."""

from __future__ import annotations

import unittest
from pathlib import Path

from shichimimi_agent.config import load_config
from shichimimi_agent.security import PolicyEngine

_X_WRITE_TOOLS = [
    "x.create_post",
    "x.delete_post",
    "x.like_post",
    "x.repost",
    "x.follow_user",
    "x.send_dm",
    "x.update_profile",
]


class PolicyEngineXWriteDenyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.config = load_config(self.root)
        self.engine = PolicyEngine(self.config.policy)
        self.roles = sorted((self.config.policy.get("role_tool_policy") or {}).keys())

    def test_role_tool_policy_is_non_empty(self) -> None:
        # Guard against this test silently passing vacuously if config
        # loading regresses to an empty policy.
        self.assertGreater(len(self.roles), 0)

    def test_every_role_denies_every_x_write_tool(self) -> None:
        for role in self.roles:
            for tool_name in _X_WRITE_TOOLS:
                with self.subTest(role=role, tool_name=tool_name):
                    decision = self.engine.decide_tool_call(role=role, tool_name=tool_name)
                    self.assertFalse(
                        decision.allowed,
                        f"role={role} tool={tool_name} was allowed; want block. reason={decision.reason}",
                    )
                    self.assertEqual(decision.decision, "block")

    def test_unknown_role_denies_every_x_write_tool(self) -> None:
        for tool_name in _X_WRITE_TOOLS:
            with self.subTest(tool_name=tool_name):
                decision = self.engine.decide_tool_call(role="nonexistent_role", tool_name=tool_name)
                self.assertFalse(decision.allowed)


if __name__ == "__main__":
    unittest.main()
