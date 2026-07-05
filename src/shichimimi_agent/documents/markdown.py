from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shichimimi_agent.util.time import iso_now, now_jst


@dataclass(frozen=True)
class TopicDigestItem:
    topic: str
    what_happened: str
    why_it_matters: str
    evidence_url: str
    x_signal_url: str
    confidence: str = "Medium"
    follow_up: str = "Check official updates and implementation details."


def render_ai_it_daily_digest(*, queries: list[str], items: list[TopicDigestItem], reviewed_posts: int, fetched_urls: int) -> str:
    today = now_jst().date().isoformat()
    lines: list[str] = [
        "---",
        f"title: Daily AI/IT Digest - {today}",
        f"date: {today}",
        "generated_by: 7mimi-agent",
        "role: ai_it_topic_runner",
        "source_policy: x_is_signal_not_evidence",
        "queries:",
    ]
    for q in queries:
        lines.append(f"  - {q!r}")
    lines.extend([
        "source_repo: 7milch/ai-it-research-notes",
        "---",
        "",
        f"# Daily AI/IT Digest - {today}",
        "",
        "## Summary",
        "",
    ])
    if items:
        for item in items[:5]:
            lines.append(f"- {item.topic}: {item.what_happened}")
    else:
        lines.append("- No notable AI/IT topics were collected in this dry run.")

    lines.extend(["", "## Top Topics", ""])
    for idx, item in enumerate(items, 1):
        primary_source = item.evidence_url if item.evidence_url else "(未確認 — 要ファクトチェック)"
        lines.extend([
            f"### {idx}. {item.topic}",
            "",
            f"- What happened: {item.what_happened}",
            f"- Why it matters: {item.why_it_matters}",
            "- Evidence:",
            f"  - Official / primary source: {primary_source}",
            "  - Supporting source: TBD",
            "- X signal:",
            f"  - Post URL: {item.x_signal_url}",
            f"- Confidence: {item.confidence}",
            f"- Follow-up: {item.follow_up}",
            "",
        ])

    lines.extend([
        "## Notable Links",
        "",
        "| Topic | Source | Type | Why notable |",
        "|---|---|---|---|",
    ])
    for item in items:
        if not item.evidence_url:
            continue
        lines.append(f"| {item.topic} | {item.evidence_url} | primary_or_project | {item.why_it_matters} |")

    lines.extend([
        "",
        "## Research Queue",
        "",
    ])
    for item in items:
        lines.extend([
            f"- [ ] {item.topic}",
            f"  - Question: {item.follow_up}",
            "  - Next source to check: official docs / GitHub / release notes",
        ])

    lines.extend([
        "",
        "## Collection Metadata",
        "",
        f"- Generated at: {iso_now()}",
        "- Queries:",
    ])
    for q in queries:
        lines.append(f"  - `{q}`")
    lines.extend([
        f"- X posts reviewed: {reviewed_posts}",
        f"- URLs fetched: {fetched_urls}",
        "",
        "## Notes",
        "",
        "- X posts are treated as signals, not evidence.",
        "- Avoid bulk reproduction of X post text.",
        "- Prefer official docs, GitHub repositories, release notes, and primary sources.",
        "",
    ])
    return "\n".join(lines)
