from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RedactionFinding:
    name: str
    pattern: str


class Redactor:
    def __init__(self, patterns: list[dict[str, str]] | None = None) -> None:
        self.patterns: list[tuple[str, re.Pattern[str]]] = []
        for item in patterns or []:
            name = item.get("name", "unnamed")
            regex = item.get("regex")
            if regex:
                self.patterns.append((name, re.compile(regex)))

    def find(self, text: str) -> list[RedactionFinding]:
        findings: list[RedactionFinding] = []
        for name, pattern in self.patterns:
            if pattern.search(text):
                findings.append(RedactionFinding(name=name, pattern=pattern.pattern))
        return findings

    def redact(self, text: str) -> str:
        redacted = text
        for name, pattern in self.patterns:
            redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
        return redacted

    def redact_obj(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_obj(v) for v in value]
        if isinstance(value, dict):
            return {k: self.redact_obj(v) for k, v in value.items()}
        return value
