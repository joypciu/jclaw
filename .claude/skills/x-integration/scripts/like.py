#!/usr/bin/env python
"""Like a tweet on X."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.browser import extract_tweet_id, read_input, with_playwright, write_result
from lib.config import CONFIG


def _tweet_url(raw: str) -> str:
    if raw.startswith("http"):
        return raw
    tid = extract_tweet_id(raw)
    return f"https://x.com/i/status/{tid}" if tid else raw


@with_playwright
def _like(_context, page, data):
    tweet_url = str(data.get("tweetUrl") or "")
    if not tweet_url:
        return {"success": False, "message": "Please provide a tweet URL"}

    page.goto(_tweet_url(tweet_url), timeout=CONFIG["timeouts"]["navigation"], wait_until="domcontentloaded")
    page.wait_for_timeout(CONFIG["timeouts"]["page_load"])

    tweet = page.locator('article[data-testid="tweet"]').first
    unlike = tweet.locator('[data-testid="unlike"]')
    like = tweet.locator('[data-testid="like"]')

    if unlike.is_visible():
        return {"success": True, "message": "Tweet already liked"}

    like.wait_for(timeout=CONFIG["timeouts"]["element_wait"])
    like.click()
    page.wait_for_timeout(CONFIG["timeouts"]["after_click"])

    if unlike.is_visible():
        return {"success": True, "message": "Like successful"}
    return {"success": False, "message": "Like action completed but could not verify success"}


def main() -> int:
    try:
        result = _like(read_input())
        write_result(result)
        return 0 if result.get("success") else 1
    except Exception as exc:
        write_result({"success": False, "message": f"Script execution failed: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
