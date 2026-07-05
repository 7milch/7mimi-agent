"""Issue #25: `collect x <query>` manual command.

Deterministic (no LLM) X signal collection for a single query. Mirrors
runner/stock_research.py's structure: x.search_posts_recent is called through
auth-proxy's /mcp endpoint under PreToolUse authorization (role x_collector).
X posts are signals, never evidence (config/policy.yaml
x_is_signal_not_evidence) -- only URL, handle, engagement, and a short
redacted snippet are stored in research_queue, never the full post text.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from shichimimi_agent.config.loader import AppConfig
from shichimimi_agent.db.repository import Repository
from shichimimi_agent.hooks.post_tool_use import run_post_tool_use
from shichimimi_agent.hooks.pre_tool_use import PreToolUseInput, run_pre_tool_use
from shichimimi_agent.mcp.client import McpHttpClient
from shichimimi_agent.proxies.auth_proxy_client import AuthProxyClient
from shichimimi_agent.security.policy_engine import PolicyEngine
from shichimimi_agent.util.time import now_jst

ROLE = "x_collector"

TOOL_NAME = "x.search_posts_recent"

_MAX_SNIPPET_CHARS = 120


@dataclass(frozen=True)
class CollectXResult:
    status: str
    query: str
    inserted_count: int
    item_ids: list[str]


def _existing_urls(repository: Repository) -> set[str]:
    urls: set[str] = set()
    for item in repository.list_research_queue():
        for ref in item.get("source_refs") or []:
            url = ref.get("url")
            if url:
                urls.add(url)
    return urls


def run_collect_x(
    *,
    config: AppConfig,
    repository: Repository,
    query: str,
    max_results: int = 20,
    session_id: str | None = None,
    task_id: str | None = None,
    auth_client: AuthProxyClient | None = None,
    mcp_client_factory: Callable[[str], McpHttpClient] | None = None,
) -> CollectXResult:
    auth_client = auth_client or AuthProxyClient(local_fallback_engine=PolicyEngine(config.policy))

    session_id = session_id or repository.create_session(source="collect-x", role=ROLE, workspace_path="")
    repository.update_session_status(session_id, "running")
    task_id = task_id or repository.create_task(
        session_id=session_id, role=ROLE, input_data={"query": query, "max_results": max_results}
    )

    try:
        mcp_url = os.environ.get("X_MCP_URL")
        if not mcp_url:
            raise RuntimeError("X_MCP_URL is not set; cannot reach the x-mcp /mcp endpoint")
        session_token = os.environ.get("X_MCP_SESSION_TOKEN")

        factory = mcp_client_factory or (lambda base_url: McpHttpClient(base_url=base_url, session_token=session_token))
        client = factory(mcp_url)
        client.initialize()

        arguments = {"query": query, "max_results": max_results}
        decision = run_pre_tool_use(
            auth_client,
            PreToolUseInput(session_id=session_id, task_id=task_id, role=ROLE, tool_name=TOOL_NAME, arguments=arguments),
        )
        if not decision.allowed:
            run_post_tool_use(
                repository,
                session_id=session_id,
                task_id=task_id,
                role=ROLE,
                tool_name=TOOL_NAME,
                decision=decision.decision,
                success=0,
                output_size=0,
            )
            raise PermissionError(decision.reason)

        result = client.call_tool(TOOL_NAME, arguments)
        content = (result.get("content") or [{}])[0]
        text_payload = content.get("text", "")
        output_size = len(text_payload.encode("utf-8"))

        run_post_tool_use(
            repository,
            session_id=session_id,
            task_id=task_id,
            role=ROLE,
            tool_name=TOOL_NAME,
            decision=decision.decision,
            success=0 if result.get("isError") else 1,
            output_size=output_size,
        )

        if result.get("isError"):
            # X MCP error text carries only the upstream status/title (never
            # credentials, which auth-proxy/x-mcp own, ADR-027 rationale
            # mirrored here) -- safe to surface verbatim.
            raise RuntimeError(f"{TOOL_NAME} failed: {text_payload}")

        try:
            payload = json.loads(text_payload or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{TOOL_NAME} returned invalid JSON") from exc

        posts = payload.get("posts") or []
        existing_urls = _existing_urls(repository)
        collected_at = now_jst().isoformat(timespec="seconds")

        item_ids: list[str] = []
        for post in posts:
            url = post.get("url") or ""
            if not url or url in existing_urls:
                continue
            author = post.get("author_handle") or post.get("author") or ""
            likes = int(post.get("like_count") or 0)
            reposts = int(post.get("repost_count") or 0)
            score = likes + reposts
            text_redacted = (post.get("text_redacted") or post.get("text") or "").strip()[:_MAX_SNIPPET_CHARS]

            item_id = repository.record_research_queue_item(
                source="x",
                topic=query,
                reason=f"X signal (engagement {score})",
                source_refs=[{"type": "url", "url": url, "author": author}],
                score=score,
                status="new",
                assigned_role=ROLE,
                metadata={
                    "post_id": post.get("id") or post.get("post_id"),
                    "collected_at": collected_at,
                    "text_redacted": text_redacted,
                },
            )
            item_ids.append(item_id)
            existing_urls.add(url)
    except Exception as exc:
        repository.finish_task(task_id, status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        repository.update_session_status(session_id, "failed")
        raise

    repository.finish_task(
        task_id,
        status="succeeded",
        output={"query": query, "inserted_count": len(item_ids), "item_ids": item_ids},
    )
    repository.update_session_status(session_id, "stopped")

    return CollectXResult(status="succeeded", query=query, inserted_count=len(item_ids), item_ids=item_ids)
