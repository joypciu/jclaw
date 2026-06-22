"""X integration browser utilities."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import CONFIG


def read_input() -> dict[str, Any]:
    import sys

    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def write_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=True))


def cleanup_lock_files() -> None:
    for name in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
        p = Path(CONFIG["browser_data_dir"]) / name
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


def validate_content(content: str | None, kind: str = "Tweet") -> dict[str, Any] | None:
    if not content:
        return {"success": False, "message": f"{kind} content cannot be empty"}
    if len(content) > int(CONFIG["limits"]["tweet_max_length"]):
        return {
            "success": False,
            "message": (
                f"{kind} exceeds {CONFIG['limits']['tweet_max_length']} character limit "
                f"(current: {len(content)})"
            ),
        }
    return None


def extract_tweet_id(value: str) -> str | None:
    import re

    m = re.search(r"(?:x\\.com|twitter\\.com)/\\w+/status/(\\d+)", value)
    if m:
        return m.group(1)
    stripped = value.strip()
    return stripped if stripped.isdigit() else None


def with_playwright(handler):
    try:
        from playwright.sync_api import sync_playwright  # pyright: ignore[reportMissingImports]
    except Exception:
        write_result({"success": False, "message": "playwright is not installed in this Python environment"})
        raise SystemExit(1)

    def _run(input_data: dict[str, Any]) -> dict[str, Any]:
        cleanup_lock_files()
        Path(CONFIG["browser_data_dir"]).mkdir(parents=True, exist_ok=True)
        Path(CONFIG["auth_path"]).parent.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(CONFIG["browser_data_dir"]),
                executable_path=str(CONFIG["chrome_path"]),
                headless=False,
                viewport=CONFIG["viewport"],
                args=CONFIG["chrome_args"],
                ignore_default_args=CONFIG["chrome_ignore_default_args"],
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                return handler(context, page, input_data)
            finally:
                context.close()

    return _run
