"""Setup step: environment check."""
from __future__ import annotations

from pathlib import Path

from src.config import STORE_DIR
from src.db import init_database

from .platform import command_exists, docker_running, get_platform, is_wsl
from .status import emit_status


def run(_args: list[str]) -> None:
    root = Path.cwd()

    # Ensure DB exists before checking tables.
    init_database()

    has_env = (root / ".env").exists()
    auth_dir = root / "store" / "auth"
    has_auth = auth_dir.exists() and any(auth_dir.iterdir())

    has_registered_groups = False
    try:
        import sqlite3
        db = sqlite3.connect(str(STORE_DIR / "messages.db"))
        cur = db.execute("SELECT COUNT(*) FROM registered_groups")
        has_registered_groups = int(cur.fetchone()[0]) > 0
        db.close()
    except Exception:
        has_registered_groups = False

    emit_status(
        "CHECK_ENVIRONMENT",
        {
            "PLATFORM": get_platform(),
            "IS_WSL": is_wsl(),
            "APPLE_CONTAINER": "installed" if command_exists("container") else "not_found",
            "DOCKER": "running" if docker_running() else ("installed_not_running" if command_exists("docker") else "not_found"),
            "HAS_ENV": has_env,
            "HAS_AUTH": has_auth,
            "HAS_REGISTERED_GROUPS": has_registered_groups,
            "STATUS": "success",
        },
    )
