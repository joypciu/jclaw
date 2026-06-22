#!/usr/bin/env python
"""X integration authentication setup."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.browser import with_playwright, write_result
from lib.config import CONFIG


@with_playwright
def _setup(_context, page, _input):
    page.goto("https://x.com/login", timeout=CONFIG["timeouts"]["navigation"], wait_until="domcontentloaded")
    input("Press Enter after you finish logging in to X in the opened browser...")

    page.goto("https://x.com/home", timeout=CONFIG["timeouts"]["navigation"], wait_until="domcontentloaded")
    page.wait_for_timeout(CONFIG["timeouts"]["page_load"])

    is_logged_in = page.locator('[data-testid="SideNav_AccountSwitcher_Button"]').is_visible()
    if not is_logged_in:
        return {"success": False, "message": "Could not verify login status. Please retry."}

    Path(CONFIG["auth_path"]).write_text(
        json.dumps({"authenticated": True, "timestamp": __import__("datetime").datetime.utcnow().isoformat()}),
        encoding="utf-8",
    )
    return {"success": True, "message": "Authentication successful"}


def main() -> int:
    try:
        result = _setup({})
        write_result(result)
        return 0 if result.get("success") else 1
    except Exception as exc:
        write_result({"success": False, "message": f"Setup failed: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
