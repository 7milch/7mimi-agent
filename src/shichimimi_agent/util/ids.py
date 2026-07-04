from __future__ import annotations

import secrets
from .time import now_jst


def new_id(prefix: str) -> str:
    ts = now_jst().strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{ts}_{secrets.token_hex(4)}"
