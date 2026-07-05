"""Minimal 5-field cron expression parser (ADR-022).

Supports the standard 5 fields: minute hour dom mon dow.
Field syntax: `*`, comma lists (`a,b`), ranges (`a-b`), steps (`*/n`, `a-b/n`).
`dow` accepts 0-6 (0=Sunday) and also 7 as an alias for Sunday.

When both dom and dow are restricted (i.e. not `*`), standard cron OR
semantics apply: a minute matches if either the dom or the dow field
matches (not both).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_FIELD_RANGES = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "mon": (1, 12),
    "dow": (0, 7),
}


def _parse_field(expr: str, lo: int, hi: int) -> set[int]:
    values: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"invalid cron field: {expr!r}")
        step = 1
        if "/" in part:
            base, step_str = part.split("/", 1)
            try:
                step = int(step_str)
            except ValueError as exc:
                raise ValueError(f"invalid step in cron field: {expr!r}") from exc
            if step <= 0:
                raise ValueError(f"invalid step in cron field: {expr!r}")
        else:
            base = part

        if base == "*":
            range_lo, range_hi = lo, hi
        elif "-" in base:
            lo_str, hi_str = base.split("-", 1)
            try:
                range_lo, range_hi = int(lo_str), int(hi_str)
            except ValueError as exc:
                raise ValueError(f"invalid range in cron field: {expr!r}") from exc
        else:
            try:
                range_lo = range_hi = int(base)
            except ValueError as exc:
                raise ValueError(f"invalid value in cron field: {expr!r}") from exc

        if range_lo > range_hi or range_lo < lo or range_hi > hi:
            raise ValueError(f"cron field out of range: {expr!r}")

        for value in range(range_lo, range_hi + 1, step):
            values.add(value)

    if not values:
        raise ValueError(f"invalid cron field: {expr!r}")
    return values


@dataclass(frozen=True)
class CronSchedule:
    minute: frozenset[int]
    hour: frozenset[int]
    dom: frozenset[int]
    mon: frozenset[int]
    dow: frozenset[int]
    dom_restricted: bool
    dow_restricted: bool
    expr: str

    @classmethod
    def parse(cls, expr: str) -> "CronSchedule":
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(f"cron expression must have 5 fields: {expr!r}")
        minute_s, hour_s, dom_s, mon_s, dow_s = fields

        minute = _parse_field(minute_s, *_FIELD_RANGES["minute"])
        hour = _parse_field(hour_s, *_FIELD_RANGES["hour"])
        dom = _parse_field(dom_s, *_FIELD_RANGES["dom"])
        mon = _parse_field(mon_s, *_FIELD_RANGES["mon"])
        dow_raw = _parse_field(dow_s, *_FIELD_RANGES["dow"])
        # normalize 7 -> 0 (Sunday)
        dow = {0 if v == 7 else v for v in dow_raw}

        return cls(
            minute=frozenset(minute),
            hour=frozenset(hour),
            dom=frozenset(dom),
            mon=frozenset(mon),
            dow=frozenset(dow),
            dom_restricted=dom_s.strip() != "*",
            dow_restricted=dow_s.strip() != "*",
            expr=expr,
        )

    def matches(self, dt: datetime) -> bool:
        if dt.minute not in self.minute:
            return False
        if dt.hour not in self.hour:
            return False
        if dt.month not in self.mon:
            return False

        dom_match = dt.day in self.dom
        dow_match = (dt.isoweekday() % 7) in self.dow  # Python Monday=1..Sunday=7 -> Sunday=0

        if self.dom_restricted and self.dow_restricted:
            return dom_match or dow_match
        return dom_match and dow_match

    def next_after(self, dt: datetime) -> datetime:
        candidate = (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
        limit = dt + timedelta(days=366)
        while candidate <= limit:
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError(f"no matching time found within search cap for cron: {self.expr!r}")
