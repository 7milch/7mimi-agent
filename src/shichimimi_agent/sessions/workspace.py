from __future__ import annotations

from pathlib import Path


def create_workspace(root: Path, session_id: str) -> Path:
    base = root / ".sessions" / session_id
    for sub in ["workspace/input", "workspace/output", "workspace/scratch", "logs", "config"]:
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base / "workspace"
