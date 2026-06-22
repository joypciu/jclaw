"""Python sidecar prototype for agent-runner migration.

This prototype intentionally does not replace the current TypeScript runtime.
It only validates the stdout output contract and stdin JSON handling.
"""
from __future__ import annotations

import json
import sys
from typing import Any

OUTPUT_START_MARKER = "---JCLAW_OUTPUT_START---"
OUTPUT_END_MARKER = "---JCLAW_OUTPUT_END---"


def _read_stdin() -> str:
    return sys.stdin.read()


def _write_output(payload: dict[str, Any]) -> None:
    sys.stdout.write(OUTPUT_START_MARKER + "\n")
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.write(OUTPUT_END_MARKER + "\n")
    sys.stdout.flush()


def main() -> int:
    try:
        raw = _read_stdin()
        data = json.loads(raw) if raw.strip() else {}
        group = str(data.get("groupFolder", "unknown"))
        prompt = str(data.get("prompt", ""))
        _write_output(
            {
                "status": "success",
                "result": f"[python-sidecar-prototype] group={group} prompt_chars={len(prompt)}",
                "newSessionId": None,
            }
        )
        return 0
    except Exception as exc:  # pragma: no cover - defensive fallback
        _write_output(
            {
                "status": "error",
                "result": None,
                "error": f"prototype_error: {exc}",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
