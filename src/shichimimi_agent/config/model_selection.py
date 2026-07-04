"""ADR-016: config-driven soft model selection.

Precedence: role.model > policy.model_policy.default_model > hardcoded fallback.
"""

from __future__ import annotations

from typing import Any

FALLBACK_MODEL = "claude-sonnet-5"


def resolve_model(role_config: dict[str, Any], policy: dict[str, Any]) -> str:
    role_config = role_config if isinstance(role_config, dict) else {}
    role_model = role_config.get("model")
    if isinstance(role_model, str) and role_model:
        return role_model

    policy = policy if isinstance(policy, dict) else {}
    model_policy = policy.get("model_policy")
    model_policy = model_policy if isinstance(model_policy, dict) else {}
    default_model = model_policy.get("default_model")
    if isinstance(default_model, str) and default_model:
        return default_model

    return FALLBACK_MODEL
