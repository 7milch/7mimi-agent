from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shichimimi_agent.security.policy_engine import PolicyDecision, PolicyEngine


@dataclass(frozen=True)
class PreToolUseInput:
    session_id: str
    role: str
    tool_name: str
    arguments: dict[str, Any]


def run_pre_tool_use(policy_engine: PolicyEngine, payload: PreToolUseInput) -> PolicyDecision:
    try:
        return policy_engine.decide_tool_call(role=payload.role, tool_name=payload.tool_name, arguments=payload.arguments)
    except Exception as exc:  # fail-closed
        return PolicyDecision("block", f"policy engine failed: {exc}")
