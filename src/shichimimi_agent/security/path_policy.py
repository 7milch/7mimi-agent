from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import PurePosixPath


@dataclass(frozen=True)
class PathDecision:
    allowed: bool
    reason: str


def _norm(path: str) -> str:
    return str(PurePosixPath(path)).lstrip("/")


def is_path_allowed(path: str, *, allowed: list[str], denied: list[str]) -> PathDecision:
    normalized = _norm(path)
    for pattern in denied:
        if fnmatch(normalized, pattern) or normalized == pattern.rstrip("/**"):
            return PathDecision(False, f"path denied by pattern {pattern}")
    if not allowed:
        return PathDecision(False, "no allowed paths configured")
    for pattern in allowed:
        if fnmatch(normalized, pattern) or normalized == pattern.rstrip("/**"):
            return PathDecision(True, "allowed")
    return PathDecision(False, "path not covered by allowed paths")
