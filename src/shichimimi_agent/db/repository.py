from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shichimimi_agent.util.ids import new_id
from shichimimi_agent.util.time import iso_now

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

    def record_research_queue_item(
        self,
        *,
        source: str,
        topic: str,
        reason: str,
        source_refs: list[dict[str, Any]],
        score: int,
        status: str = "new",
        assigned_role: str | None = None,
        ticker: str | None = None,
        company_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        item_id = new_id("rq")
        now = iso_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_queue (
                  id, source, topic, ticker, company_name, reason, source_refs_json,
                  score, status, assigned_role, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    source,
                    topic,
                    ticker,
                    company_name,
                    reason,
                    json.dumps(source_refs, ensure_ascii=False),
                    score,
                    status,
                    assigned_role,
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        return item_id

    def list_research_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM research_queue"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at ASC"
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["source_refs"] = json.loads(item.pop("source_refs_json") or "[]")
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            items.append(item)
        return items

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
