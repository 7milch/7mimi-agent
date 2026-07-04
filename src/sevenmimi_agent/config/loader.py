from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AppConfig:
    root: Path
    roles: dict[str, Any]
    policy: dict[str, Any]
    schedules: dict[str, Any]

    @property
    def role_names(self) -> set[str]:
        return set((self.roles.get("roles") or {}).keys())

    @property
    def mcp_server_names(self) -> set[str]:
        return set((self.policy.get("mcp_servers") or {}).keys())


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "config" / "roles.yaml").exists():
            return candidate
    raise FileNotFoundError("Could not find project root containing config/roles.yaml")


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be mapping: {path}")
    return data


def load_config(root: Path | None = None) -> AppConfig:
    root = find_project_root(root)
    return AppConfig(
        root=root,
        roles=_load_yaml(root / "config" / "roles.yaml"),
        policy=_load_yaml(root / "config" / "policy.yaml"),
        schedules=_load_yaml(root / "config" / "schedules.yaml"),
    )
