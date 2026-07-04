from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any

from sevenmimi_agent.security.path_policy import is_path_allowed


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    reason: str
    policy_version: str = "1"

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class PolicyEngine:
    def __init__(self, policy: dict[str, Any]) -> None:
        self.policy = policy
        self.role_tool_policy = policy.get("role_tool_policy") or {}
        self.document_repositories = policy.get("document_repositories") or {}

    def decide_tool_call(self, *, role: str, tool_name: str, arguments: dict[str, Any] | None = None) -> PolicyDecision:
        arguments = arguments or {}
        role_policy = self.role_tool_policy.get(role)
        if role_policy is None:
            return PolicyDecision("block", f"unknown role or missing role_tool_policy: {role}")
        for pattern in role_policy.get("deny") or []:
            if fnmatch(tool_name, pattern):
                return PolicyDecision("block", f"tool denied for role {role}: {pattern}")
        allowed = role_policy.get("allow") or []
        if not any(fnmatch(tool_name, pattern) for pattern in allowed):
            return PolicyDecision("block", f"tool not allowed for role {role}: {tool_name}")
        if tool_name in {"document.write_markdown", "document.commit_and_push_markdown_repo"}:
            repo = arguments.get("repo")
            path = arguments.get("path")
            if repo and path:
                repo_policy = self._find_repo_policy(repo)
                if repo_policy is None:
                    return PolicyDecision("block", f"unknown document repository: {repo}")
                path_decision = is_path_allowed(path, allowed=repo_policy.get("allowed_paths") or [], denied=repo_policy.get("denied_paths") or [])
                if not path_decision.allowed:
                    return PolicyDecision("block", path_decision.reason)
        return PolicyDecision("allow", "allowed")

    def _find_repo_policy(self, repo: str) -> dict[str, Any] | None:
        for policy in self.document_repositories.values():
            if policy.get("repo") == repo:
                return policy
        return None
