"""Setup status output helper."""
from __future__ import annotations

import json


def emit_status(step: str, payload: dict[str, object]) -> None:
    out = {"step": step, **payload}
    print(json.dumps(out, ensure_ascii=True))
