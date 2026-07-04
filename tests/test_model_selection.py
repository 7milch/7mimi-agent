from __future__ import annotations

import unittest

from shichimimi_agent.config.model_selection import resolve_model


class ResolveModelTest(unittest.TestCase):
    def test_role_model_takes_precedence(self) -> None:
        role = {"model": "claude-opus-4-8"}
        policy = {"model_policy": {"default_model": "claude-sonnet-5"}}
        self.assertEqual(resolve_model(role, policy), "claude-opus-4-8")

    def test_falls_back_to_default_model(self) -> None:
        role = {}
        policy = {"model_policy": {"default_model": "claude-sonnet-5"}}
        self.assertEqual(resolve_model(role, policy), "claude-sonnet-5")

    def test_falls_back_to_hardcoded_when_no_policy(self) -> None:
        self.assertEqual(resolve_model({}, {}), "claude-sonnet-5")

    def test_empty_role_model_falls_through(self) -> None:
        role = {"model": ""}
        policy = {"model_policy": {"default_model": "claude-sonnet-5"}}
        self.assertEqual(resolve_model(role, policy), "claude-sonnet-5")

    def test_missing_model_policy_section_falls_back_to_hardcoded(self) -> None:
        self.assertEqual(resolve_model({}, {"some_other_key": {}}), "claude-sonnet-5")

    def test_malformed_model_policy_type_does_not_crash(self) -> None:
        # model_policy should be a mapping per config/policy.yaml; if it is
        # malformed (e.g. a string) resolve_model must degrade to the
        # hardcoded fallback rather than raising.
        role: dict = {}
        policy = {"model_policy": "not-a-dict"}
        self.assertEqual(resolve_model(role, policy), "claude-sonnet-5")

    def test_role_config_none_falls_back(self) -> None:
        policy = {"model_policy": {"default_model": "claude-sonnet-5"}}
        self.assertEqual(resolve_model(None, policy), "claude-sonnet-5")

    def test_policy_none_falls_back_to_hardcoded(self) -> None:
        self.assertEqual(resolve_model({}, None), "claude-sonnet-5")


if __name__ == "__main__":
    unittest.main()
