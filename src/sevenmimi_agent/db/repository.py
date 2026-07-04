from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sevenmimi_agent.util.ids import new_id
from sevenmimi_agent.util.time import iso_now

from .migrations import connect, default_db_path


@dataclass
class Repository:
    db_path: Path

    @classmethod
    def for_root(cls, root: Path) -> "Repository":
        return cls(default_db_path(root))

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def create_session(self, *, source: str, role: str, workspace_path: str, metadata: dict[str, Any] | None = None) -> str:
        session_id = new_id("sess")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, source, role, status, workspace_path, created_at, updated_at, last_active_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, source, role, "created", workspace_path, now, now, now, json.dumps(metadata or {}, ensure_ascii=False)),
            )
        return session_id

    def update_session_status(self, session_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE sessions SET status = ?, updated_at = ?, last_active_at = ? WHERE id = ?", (status, iso_now(), iso_now(), session_id))

    def create_task(self, *, session_id: str, role: str, input_data: dict[str, Any]) -> str:
        task_id = new_id("task")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, session_id, role, status, input_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, session_id, role, "queued", json.dumps(input_data, ensure_ascii=False), now),
            )
        return task_id

    def finish_task(self, task_id: str, *, status: str, output: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, output_json = ?, error_json = ?, finished_at = ? WHERE id = ?",
                (
                    status,
                    json.dumps(output, ensure_ascii=False) if output is not None else None,
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                    iso_now(),
                    task_id,
                ),
            )

    def record_tool_event(self, **event: Any) -> str:
        event_id = event.get("id") or new_id("tool")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_events (
                  id, session_id, task_id, role, tool_name, decision, success, duration_ms,
                  input_hash, input_redacted_json, output_hash, output_size, error_json,
                  policy_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event["session_id"],
                    event.get("task_id"),
                    event["role"],
                    event["tool_name"],
                    event["decision"],
                    event.get("success"),
                    event.get("duration_ms"),
                    event.get("input_hash"),
                    json.dumps(event.get("input_redacted"), ensure_ascii=False) if event.get("input_redacted") is not None else None,
                    event.get("output_hash"),
                    event.get("output_size"),
                    json.dumps(event.get("error"), ensure_ascii=False) if event.get("error") is not None else None,
                    event.get("policy_version", "1"),
                    event.get("created_at", iso_now()),
                ),
            )
        return event_id

    def record_document(self, *, repo: str | None, path: str, title: str, doc_type: str, status: str, source_refs: list[dict[str, Any]], commit_sha: str | None = None, metadata: dict[str, Any] | None = None) -> str:
        doc_id = new_id("doc")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (id, repo, path, title, doc_type, status, source_refs_json, commit_sha, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, repo, path, title, doc_type, status, json.dumps(source_refs, ensure_ascii=False), commit_sha, now, now, json.dumps(metadata or {}, ensure_ascii=False)),
            )
        return doc_id
