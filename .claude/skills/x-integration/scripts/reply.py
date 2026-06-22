#!/usr/bin/env python
"""Reply to a tweet on X."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.browser import extract_tweet_id, read_input, validate_content, with_playwright, write_result
from lib.config import CONFIG


def _tweet_url(raw: str) -> str:
    if raw.startswith("http"):
        return raw
    tid = extract_tweet_id(raw)
    return f"https://x.com/i/status/{tid}" if tid else raw


@with_playwright
def _reply(_context, page, data):
    tweet_url = str(data.get("tweetUrl") or "")
    content = data.get("content")
    if not tweet_url:
        return {"success": False, "message": "Please provide a tweet URL"}

    err = validate_content(content, "Reply")
    if err:
        return err

    page.goto(_tweet_url(tweet_url), timeout=CONFIG["timeouts"]["navigation"], wait_until="domcontentloaded")
    page.wait_for_timeout(CONFIG["timeouts"]["page_load"])

    tweet = page.locator('article[data-testid="tweet"]').first
    reply_btn = tweet.locator('[data-testid="reply"]')
    reply_btn.wait_for(timeout=CONFIG["timeouts"]["element_wait"])
    reply_btn.click()
    page.wait_for_timeout(int(CONFIG["timeouts"]["after_click"] * 1.5))

    dialog = page.locator('[role="dialog"][aria-modal="true"]')
    dialog.wait_for(timeout=CONFIG["timeouts"]["element_wait"])
    reply_input = dialog.locator('[data-testid="tweetTextarea_0"]')
    reply_input.wait_for(timeout=CONFIG["timeouts"]["element_wait"])
    reply_input.click()
    reply_input.fill(content)
    page.wait_for_timeout(CONFIG["timeouts"]["after_fill"])

    submit = dialog.locator('[data-testid="tweetButton"]')
    submit.wait_for(timeout=CONFIG["timeouts"]["element_wait"])
    if submit.get_attribute("aria-disabled") == "true":
        return {"success": False, "message": "Submit button disabled. Content may be invalid."}

    submit.click()
    page.wait_for_timeout(CONFIG["timeouts"]["after_submit"])
    preview = content[:50] + ("..." if len(content) > 50 else "")
    return {"success": True, "message": f"Reply posted: {preview}"}


def main() -> int:
    try:
        result = _reply(read_input())
        write_result(result)
        return 0 if result.get("success") else 1
    except Exception as exc:
        write_result({"success": False, "message": f"Script execution failed: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
