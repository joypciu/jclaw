"""Setup step: groups sync/list for setup flow."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from src.config import STORE_DIR

from .status import emit_status


def _arg_flag(args: list[str], flag: str) -> bool:
    return flag in args


def _arg_int(args: list[str], key: str, default: int) -> int:
    for i, token in enumerate(args):
        if token == key and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except Exception:
                return default
    return default


def _list_groups(limit: int) -> None:
    db_path = STORE_DIR / "messages.db"
    if not db_path.exists():
        raise SystemExit("database not found")

    db = sqlite3.connect(str(db_path))
    cur = db.execute(
        """
        SELECT jid, name FROM chats
        WHERE jid LIKE '%@g.us' AND jid <> '__group_sync__' AND name <> jid
        ORDER BY last_message_time DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    db.close()

    for jid, name in rows:
        print(f"{jid}|{name}")


def run(args: list[str]) -> None:
    if _arg_flag(args, "--list"):
        _list_groups(_arg_int(args, "--limit", 30))
        return

    project_root = Path.cwd()
    auth_dir = project_root / "store" / "auth"
    has_whatsapp_auth = auth_dir.exists() and any(auth_dir.iterdir())

    if not has_whatsapp_auth:
        emit_status(
            "SYNC_GROUPS",
            {
                "BUILD": "skipped",
                "SYNC": "skipped",
                "GROUPS_IN_DB": 0,
                "REASON": "whatsapp_not_configured",
                "STATUS": "success",
            },
        )
        return

    # Group metadata is discovered by running channel integrations.
    emit_status(
        "SYNC_GROUPS",
        {
            "BUILD": "skipped",
            "SYNC": "skipped",
            "GROUPS_IN_DB": 0,
            "REASON": "run_channel_sync",
            "STATUS": "success",
        },
    )
