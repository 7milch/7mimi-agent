from __future__ import annotations

from pathlib import Path
import unittest

from shichimimi_agent.config import load_config, validate_config
from shichimimi_agent.security import PolicyEngine, is_path_allowed


class FoundationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.config = load_config(self.root)

    def test_config_validates(self) -> None:
        result = validate_config(self.config)
        self.assertEqual(result.errors, [])

    def test_x_write_is_blocked_for_ai_it_runner(self) -> None:
        engine = PolicyEngine(self.config.policy)
        decision = engine.decide_tool_call(role="ai_it_topic_runner", tool_name="x.create_post", arguments={})
        self.assertFalse(decision.allowed)

    def test_ai_it_repo_path_policy(self) -> None:
        repo = self.config.policy["document_repositories"]["ai_it_research_notes"]
        allowed = is_path_allowed("daily/2026/07/2026-07-04.md", allowed=repo["allowed_paths"], denied=repo["denied_paths"])
        denied = is_path_allowed(".github/workflows/pwn.yml", allowed=repo["allowed_paths"], denied=repo["denied_paths"])
        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)


if __name__ == "__main__":
    unittest.main()
