from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WriteResult:
    path: Path
    repo: str | None
    pushed: bool
    commit_sha: str | None = None


class DocumentRepositoryWriter:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write_dry_run(self, *, relative_path: str, content: str) -> WriteResult:
        output_path = self.root / ".data" / "dry-run" / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return WriteResult(path=output_path, repo=None, pushed=False)
