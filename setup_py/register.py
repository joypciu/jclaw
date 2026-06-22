"""Setup step: register a group/channel in SQLite and filesystem."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.config import STORE_DIR
from src.db import init_database, set_registered_group
from src.group_folder import is_valid_group_folder
from src.types import RegisteredGroup

from .status import emit_status


@dataclass
class RegisterArgs:
    jid: str = ""
    name: str = ""
    trigger: str = ""
    folder: str = ""
    channel: str = "whatsapp"
    requires_trigger: bool = True
    is_main: bool = False


def _parse_args(args: list[str]) -> RegisterArgs:
    out = RegisterArgs()
    i = 0
    while i < len(args):
        token = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if token == "--jid":
            out.jid = nxt
            i += 2
            continue
        if token == "--name":
            out.name = nxt
            i += 2
            continue
        if token == "--trigger":
            out.trigger = nxt
            i += 2
            continue
        if token == "--folder":
            out.folder = nxt
            i += 2
            continue
        if token == "--channel":
            out.channel = nxt.lower()
            i += 2
            continue
        if token == "--no-trigger-required":
            out.requires_trigger = False
            i += 1
            continue
        if token == "--is-main":
            out.is_main = True
            i += 1
            continue
        i += 1
    return out


def run(args: list[str]) -> None:
    parsed = _parse_args(args)

    if not parsed.jid or not parsed.name or not parsed.trigger or not parsed.folder:
        emit_status(
            "REGISTER_CHANNEL",
            {
                "STATUS": "failed",
                "ERROR": "missing_required_args",
            },
        )
        raise SystemExit(4)

    if not is_valid_group_folder(parsed.folder):
        emit_status(
            "REGISTER_CHANNEL",
            {
                "STATUS": "failed",
                "ERROR": "invalid_folder",
            },
        )
        raise SystemExit(4)

    project_root = Path.cwd()
    (project_root / "data").mkdir(parents=True, exist_ok=True)
    STORE_DIR.mkdir(parents=True, exist_ok=True)

    init_database()
    set_registered_group(
        parsed.jid,
        RegisteredGroup(
            name=parsed.name,
            folder=parsed.folder,
            trigger=parsed.trigger,
            added_at=datetime.now(timezone.utc).isoformat(),
            requires_trigger=parsed.requires_trigger,
            is_main=True if parsed.is_main else None,
        ),
    )

    (project_root / "groups" / parsed.folder / "logs").mkdir(parents=True, exist_ok=True)

    emit_status(
        "REGISTER_CHANNEL",
        {
            "JID": parsed.jid,
            "NAME": parsed.name,
            "FOLDER": parsed.folder,
            "CHANNEL": parsed.channel,
            "TRIGGER": parsed.trigger,
            "REQUIRES_TRIGGER": parsed.requires_trigger,
            "STATUS": "success",
        },
    )
