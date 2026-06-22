"""X integration configuration."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("NANOCLAW_ROOT") or Path.cwd())

CONFIG = {
    "chrome_path": os.environ.get(
        "CHROME_PATH",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ),
    "browser_data_dir": PROJECT_ROOT / "data" / "x-browser-profile",
    "auth_path": PROJECT_ROOT / "data" / "x-auth.json",
    "viewport": {"width": 1280, "height": 800},
    "timeouts": {
        "navigation": 30000,
        "element_wait": 5000,
        "after_click": 1000,
        "after_fill": 1000,
        "after_submit": 3000,
        "page_load": 3000,
    },
    "limits": {"tweet_max_length": 280},
    "chrome_args": [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
    ],
    "chrome_ignore_default_args": ["--enable-automation"],
}
