"""Sender allowlist loading and enforcement for inbound triggers."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .config import SENDER_ALLOWLIST_PATH
from .logger import logger


AllowMode = Literal["trigger", "drop"]


@dataclass
class ChatAllowlistEntry:
    allow: list[str] | str  # "*" or specific sender IDs
    mode: AllowMode


@dataclass
class SenderAllowlistConfig:
    default: ChatAllowlistEntry
    chats: dict[str, ChatAllowlistEntry] = field(default_factory=dict)
    log_denied: bool = True


def _default_config() -> SenderAllowlistConfig:
    return SenderAllowlistConfig(
        default=ChatAllowlistEntry(allow="*", mode="trigger"),
        chats={},
        log_denied=True,
    )


def _is_valid_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False

    allow = entry.get("allow")
    mode = entry.get("mode")

    valid_allow = allow == "*" or (
        isinstance(allow, list) and all(isinstance(v, str) for v in allow)
    )
    valid_mode = mode in ("trigger", "drop")
    return bool(valid_allow and valid_mode)


def _parse_entry(entry: object) -> ChatAllowlistEntry:
    if not isinstance(entry, dict):
        raise ValueError("allowlist entry must be an object")

    allow = entry.get("allow")
    mode = entry.get("mode")

    if allow != "*" and not (
        isinstance(allow, list) and all(isinstance(v, str) for v in allow)
    ):
        raise ValueError("allowlist entry has invalid allow value")
    if mode not in ("trigger", "drop"):
        raise ValueError("allowlist entry has invalid mode")

    return ChatAllowlistEntry(
        allow=allow,
        mode=mode,
    )


def load_sender_allowlist(
    path_override: str | Path | None = None,
) -> SenderAllowlistConfig:
    file_path = Path(path_override) if path_override is not None else Path(SENDER_ALLOWLIST_PATH)

    try:
        raw = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default_config()
    except Exception as exc:
        logger.warning("sender-allowlist: cannot read config at %s (%s)", file_path, exc)
        return _default_config()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("sender-allowlist: invalid JSON at %s", file_path)
        return _default_config()

    if not isinstance(parsed, dict):
        logger.warning("sender-allowlist: config root must be an object at %s", file_path)
        return _default_config()

    default_entry = parsed.get("default")
    if not _is_valid_entry(default_entry):
        logger.warning("sender-allowlist: invalid or missing default entry at %s", file_path)
        return _default_config()

    chats: dict[str, ChatAllowlistEntry] = {}
    chats_obj = parsed.get("chats")
    if isinstance(chats_obj, dict):
        for jid, entry in chats_obj.items():
            if not isinstance(jid, str):
                continue
            if _is_valid_entry(entry):
                chats[jid] = _parse_entry(entry)
            else:
                logger.warning(
                    "sender-allowlist: skipping invalid chat entry for %s in %s",
                    jid,
                    file_path,
                )

    log_denied = parsed.get("logDenied") is not False

    return SenderAllowlistConfig(
        default=_parse_entry(default_entry),
        chats=chats,
        log_denied=log_denied,
    )


def _get_entry(chat_jid: str, cfg: SenderAllowlistConfig) -> ChatAllowlistEntry:
    return cfg.chats.get(chat_jid, cfg.default)


def is_sender_allowed(chat_jid: str, sender: str, cfg: SenderAllowlistConfig) -> bool:
    entry = _get_entry(chat_jid, cfg)
    if entry.allow == "*":
        return True
    return sender in entry.allow


def should_drop_message(chat_jid: str, cfg: SenderAllowlistConfig) -> bool:
    return _get_entry(chat_jid, cfg).mode == "drop"


def is_trigger_allowed(chat_jid: str, sender: str, cfg: SenderAllowlistConfig) -> bool:
    allowed = is_sender_allowed(chat_jid, sender, cfg)
    if not allowed and cfg.log_denied:
        logger.debug(
            "sender-allowlist: trigger denied for sender %s in chat %s",
            sender,
            chat_jid,
        )
    return allowed
