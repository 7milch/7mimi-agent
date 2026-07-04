from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .loader import AppConfig


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)

    def warn_if(self, condition: bool, message: str) -> None:
        if condition:
            self.warnings.append(message)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def validate_config(config: AppConfig) -> ValidationResult:
    result = ValidationResult()

    roles = config.roles.get("roles") or {}
    policy = config.policy
    schedules = config.schedules
    mcp_servers = policy.get("mcp_servers") or {}
    role_tool_policy = policy.get("role_tool_policy") or {}

    result.require(isinstance(roles, dict) and bool(roles), "roles.yaml must define roles")
    result.require(isinstance(mcp_servers, dict) and bool(mcp_servers), "policy.yaml must define mcp_servers")

    for role_name, role in roles.items():
        for server_name in _as_list(role.get("mcp_servers")):
            result.require(
                server_name in mcp_servers,
                f"role {role_name} references unknown mcp server: {server_name}",
            )
        result.warn_if(role_name not in role_tool_policy and role_name != "orchestrator", f"role {role_name} has no role_tool_policy")

    for job in _as_list(schedules.get("jobs")):
        name = job.get("name", "<unnamed>")
        role_name = job.get("role")
        result.require(role_name in roles, f"schedule job {name} references unknown role: {role_name}")
        query_set = (job.get("inputs") or {}).get("query_set")
        if query_set:
            result.require(query_set in (schedules.get("query_sets") or {}), f"schedule job {name} references unknown query_set: {query_set}")

    principles = policy.get("principles") or {}
    result.require(principles.get("pre_tool_use_failure") == "block", "PreToolUse must fail closed")
    result.require(principles.get("post_tool_use_failure") == "continue", "PostToolUse must fail open")
    result.require(principles.get("agent_runner_has_claude_credentials") is False, "agent-runner must not have Claude credentials")

    claude_proxy = policy.get("claude_proxy") or {}
    result.require(claude_proxy.get("runner_receives_provider_token") is False, "runner must not receive provider token")
    allowed_env = set(_as_list(claude_proxy.get("allowed_runner_env")))
    for required in {"CLAUDE_PROXY_URL", "CLAUDE_PROXY_SESSION_TOKEN", "SESSION_ID", "ROLE"}:
        result.require(required in allowed_env, f"claude_proxy.allowed_runner_env missing {required}")
    denied_env = set(_as_list(claude_proxy.get("denied_runner_env")))
    for denied in {"ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_SUBSCRIPTION_TOKEN", "CLAUDE_CONFIG_DIR"}:
        result.require(denied in denied_env, f"claude_proxy.denied_runner_env missing {denied}")

    x_policy = mcp_servers.get("x_mcp_readonly") or {}
    x_denied = set(_as_list(x_policy.get("deny_tools")))
    for tool in {"x.create_post", "x.like_post", "x.repost", "x.follow_user", "x.send_dm"}:
        result.require(tool in x_denied, f"x_mcp_readonly must deny {tool}")

    repos = policy.get("document_repositories") or {}
    for repo_name, repo in repos.items():
        allowed_paths = _as_list(repo.get("allowed_paths"))
        denied_paths = _as_list(repo.get("denied_paths"))
        result.require(bool(allowed_paths), f"document repo {repo_name} must define allowed_paths")
        for denied in [".github/**", ".env", "secrets/**"]:
            result.require(denied in denied_paths, f"document repo {repo_name} denied_paths missing {denied}")

    return result
