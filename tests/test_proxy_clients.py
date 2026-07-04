from __future__ import annotations

from pathlib import Path
import unittest

from shichimimi_agent.config import load_config
from shichimimi_agent.proxies import AuthProxyClient
from shichimimi_agent.security import PolicyEngine


class AuthProxyClientTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.engine = PolicyEngine(load_config(root).policy)

    def test_local_fallback_allows_search(self) -> None:
        client = AuthProxyClient(base_url="", local_fallback_engine=self.engine)
        decision = client.authorize(
            session_id="sess_dev", task_id="task_dev", role="ai_it_topic_runner",
            tool_name="x.search_posts_recent", arguments={"query": '"Claude Code"'},
        )
        self.assertTrue(decision.allowed)

    def test_local_fallback_blocks_x_write(self) -> None:
        client = AuthProxyClient(base_url="", local_fallback_engine=self.engine)
        decision = client.authorize(
            session_id="sess_dev", task_id="task_dev", role="ai_it_topic_runner",
            tool_name="x.create_post", arguments={},
        )
        self.assertFalse(decision.allowed)

    def test_remote_failure_is_fail_closed(self) -> None:
        # Remote configured but unreachable: must block, not fall back.
        client = AuthProxyClient(
            base_url="http://127.0.0.1:1", local_fallback_engine=self.engine, timeout_seconds=0.2
        )
        decision = client.authorize(
            session_id="sess_dev", task_id="task_dev", role="ai_it_topic_runner",
            tool_name="x.search_posts_recent", arguments={},
        )
        self.assertFalse(decision.allowed)

    def test_no_proxy_and_no_engine_blocks(self) -> None:
        client = AuthProxyClient(base_url="", local_fallback_engine=None)
        decision = client.authorize(
            session_id="sess_dev", task_id="task_dev", role="ai_it_topic_runner",
            tool_name="x.search_posts_recent", arguments={},
        )
        self.assertFalse(decision.allowed)


if __name__ == "__main__":
    unittest.main()
