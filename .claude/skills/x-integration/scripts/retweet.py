#!/usr/bin/env python
"""Retweet on X."""
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
def _retweet(_context, page, data):
    tweet_url = str(data.get("tweetUrl") or "")
    if not tweet_url:
        return {"success": False, "message": "Please provide a tweet URL"}

    page.goto(_tweet_url(tweet_url), timeout=CONFIG["timeouts"]["navigation"], wait_until="domcontentloaded")
    page.wait_for_timeout(CONFIG["timeouts"]["page_load"])

    tweet = page.locator('article[data-testid="tweet"]').first
    unretweet = tweet.locator('[data-testid="unretweet"]')
    retweet = tweet.locator('[data-testid="retweet"]')

    if unretweet.is_visible():
        return {"success": True, "message": "Tweet already retweeted"}

    retweet.wait_for(timeout=CONFIG["timeouts"]["element_wait"])
    retweet.click()
    page.wait_for_timeout(CONFIG["timeouts"]["after_click"])

    confirm = page.locator('[data-testid="retweetConfirm"]')
    confirm.wait_for(timeout=CONFIG["timeouts"]["element_wait"])
    confirm.click()
    page.wait_for_timeout(int(CONFIG["timeouts"]["after_click"] * 2))

    if unretweet.is_visible():
        return {"success": True, "message": "Retweet successful"}
    return {"success": False, "message": "Retweet action completed but could not verify success"}


def main() -> int:
    try:
        result = _retweet(read_input())
        write_result(result)
        return 0 if result.get("success") else 1
    except Exception as exc:
        write_result({"success": False, "message": f"Script execution failed: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
