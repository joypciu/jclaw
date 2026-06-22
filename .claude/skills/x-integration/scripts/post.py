#!/usr/bin/env python
"""Post a tweet on X."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.browser import read_input, validate_content, with_playwright, write_result
from lib.config import CONFIG


@with_playwright
def _post(_context, page, data):
    content = data.get("content")
    err = validate_content(content, "Tweet")
    if err:
        return err

    page.goto("https://x.com/home", timeout=CONFIG["timeouts"]["navigation"], wait_until="domcontentloaded")
    page.wait_for_timeout(CONFIG["timeouts"]["page_load"])

    tweet_input = page.locator('[data-testid="tweetTextarea_0"]')
    tweet_input.wait_for(timeout=CONFIG["timeouts"]["element_wait"] * 2)
    tweet_input.click()
    page.wait_for_timeout(CONFIG["timeouts"]["after_click"] // 2)
    tweet_input.fill(content)
    page.wait_for_timeout(CONFIG["timeouts"]["after_fill"])

    post_button = page.locator('[data-testid="tweetButtonInline"]')
    post_button.wait_for(timeout=CONFIG["timeouts"]["element_wait"])
    if post_button.get_attribute("aria-disabled") == "true":
        return {"success": False, "message": "Post button disabled. Content may be invalid."}

    post_button.click()
    page.wait_for_timeout(CONFIG["timeouts"]["after_submit"])
    preview = content[:50] + ("..." if len(content) > 50 else "")
    return {"success": True, "message": f"Tweet posted: {preview}"}


def main() -> int:
    try:
        result = _post(read_input())
        write_result(result)
        return 0 if result.get("success") else 1
    except Exception as exc:
        write_result({"success": False, "message": f"Script execution failed: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
