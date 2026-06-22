"""Setup step: write mount allowlist config."""
from __future__ import annotations

import json

from src.config import MOUNT_ALLOWLIST_PATH

from .status import emit_status


def _arg_flag(args: list[str], flag: str) -> bool:
    return flag in args


def _arg_value(args: list[str], key: str, default: str = "") -> str:
    for i, token in enumerate(args):
        if token == key and i + 1 < len(args):
            return args[i + 1]
    return default


def _write_config(payload: dict[str, object]) -> tuple[int, str]:
    MOUNT_ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOUNT_ALLOWLIST_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    allowed_roots = payload.get("allowedRoots")
    count = len(allowed_roots) if isinstance(allowed_roots, list) else 0
    non_main_read_only = "false" if payload.get("nonMainReadOnly") is False else "true"
    return count, non_main_read_only


def run(args: list[str]) -> None:
    empty = _arg_flag(args, "--empty")
    json_text = _arg_value(args, "--json")

    if empty:
        payload = {
            "allowedRoots": [],
            "blockedPatterns": [],
            "nonMainReadOnly": True,
        }
    elif json_text:
        try:
            payload = json.loads(json_text)
        except Exception:
            emit_status(
                "CONFIGURE_MOUNTS",
                {
                    "PATH": str(MOUNT_ALLOWLIST_PATH),
                    "ALLOWED_ROOTS": 0,
                    "NON_MAIN_READ_ONLY": "unknown",
                    "STATUS": "failed",
                    "ERROR": "invalid_json",
                },
            )
            raise SystemExit(4)
        if not isinstance(payload, dict):
            emit_status(
                "CONFIGURE_MOUNTS",
                {
                    "PATH": str(MOUNT_ALLOWLIST_PATH),
                    "ALLOWED_ROOTS": 0,
                    "NON_MAIN_READ_ONLY": "unknown",
                    "STATUS": "failed",
                    "ERROR": "invalid_json",
                },
            )
            raise SystemExit(4)
    else:
        raw = ""
        try:
            import sys
            raw = sys.stdin.read()
            payload = json.loads(raw)
        except Exception:
            emit_status(
                "CONFIGURE_MOUNTS",
                {
                    "PATH": str(MOUNT_ALLOWLIST_PATH),
                    "ALLOWED_ROOTS": 0,
                    "NON_MAIN_READ_ONLY": "unknown",
                    "STATUS": "failed",
                    "ERROR": "invalid_json",
                },
            )
            raise SystemExit(4)
        if not isinstance(payload, dict):
            emit_status(
                "CONFIGURE_MOUNTS",
                {
                    "PATH": str(MOUNT_ALLOWLIST_PATH),
                    "ALLOWED_ROOTS": 0,
                    "NON_MAIN_READ_ONLY": "unknown",
                    "STATUS": "failed",
                    "ERROR": "invalid_json",
                },
            )
            raise SystemExit(4)

    allowed_roots, non_main_read_only = _write_config(payload)
    emit_status(
        "CONFIGURE_MOUNTS",
        {
            "PATH": str(MOUNT_ALLOWLIST_PATH),
            "ALLOWED_ROOTS": allowed_roots,
            "NON_MAIN_READ_ONLY": non_main_read_only,
            "STATUS": "success",
        },
    )
