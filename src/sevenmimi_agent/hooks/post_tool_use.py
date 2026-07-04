from __future__ import annotations

from typing import Any

from sevenmimi_agent.db.repository import Repository


def run_post_tool_use(repository: Repository, **event: Any) -> None:
    try:
        repository.record_tool_event(**event)
    except Exception:
        # fail-open: metrics must not break agent execution
        return
