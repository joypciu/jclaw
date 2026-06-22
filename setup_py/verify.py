"""Setup step: verify installation health."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.config import STORE_DIR

from .platform import command_exists, docker_running, get_service_manager
from .status import emit_status


def _service_status() -> str:
    mgr = get_service_manager()
    if mgr == "launchd":
        return "unknown"
    if mgr == "systemd":
        return "unknown"
    pid_file = Path.cwd() / "nanoclaw.pid"
    return "running" if pid_file.exists() else "stopped"


def run(_args: list[str]) -> None:
    root = Path.cwd()
    service = _service_status()

    env_file = root / ".env"
    credentials = "missing"
    if env_file.exists():
        txt = env_file.read_text(encoding="utf-8")
        if "CLAUDE_CODE_OAUTH_TOKEN=" in txt or "ANTHROPIC_API_KEY=" in txt:
            credentials = "configured"

    channel_auth: dict[str, str] = {}
    if (root / "store" / "auth").exists():
        channel_auth["whatsapp"] = "authenticated"

    try:
        txt = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        if "TELEGRAM_BOT_TOKEN=" in txt:
            channel_auth["telegram"] = "configured"
        if "SLACK_BOT_TOKEN=" in txt and "SLACK_APP_TOKEN=" in txt:
            channel_auth["slack"] = "configured"
        if "DISCORD_BOT_TOKEN=" in txt:
            channel_auth["discord"] = "configured"
    except Exception:
        pass

    registered_groups = 0
    try:
        db = sqlite3.connect(str(STORE_DIR / "messages.db"))
        cur = db.execute("SELECT COUNT(*) FROM registered_groups")
        registered_groups = int(cur.fetchone()[0])
        db.close()
    except Exception:
        pass

    status = (
        "success"
        if credentials == "configured" and len(channel_auth) > 0 and registered_groups > 0
        else "failed"
    )

    emit_status(
        "VERIFY",
        {
            "SERVICE": service,
            "CONTAINER_RUNTIME": "docker" if docker_running() else ("container" if command_exists("container") else "none"),
            "CREDENTIALS": credentials,
            "CONFIGURED_CHANNELS": ",".join(sorted(channel_auth.keys())),
            "CHANNEL_AUTH": json.dumps(channel_auth),
            "REGISTERED_GROUPS": registered_groups,
            "STATUS": status,
        },
    )
