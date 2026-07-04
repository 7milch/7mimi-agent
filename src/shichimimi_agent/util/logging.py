from __future__ import annotations

import json
import sys
from typing import Any

from .time import iso_now


def log(event: str, **fields: Any) -> None:
    payload = {"ts": iso_now(), "event": event, **fields}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
