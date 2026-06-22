"""Runtime feature flags for J Claw.

Inspired by claw-code's build-time feature gates and openclaude's provider
capability matrix, this module provides a unified runtime flag system.

Configuration
─────────────
Set JCLAW_FEATURES in .env or the environment.  Flags are comma-separated.
Prefix a flag with ``-`` to explicitly disable it.

Examples
--------
# Enable everything (default when var is absent)
JCLAW_FEATURES=*

# Enable only specific capabilities
JCLAW_FEATURES=web,parallel,vision

# Enable all except voice and remote_control
JCLAW_FEATURES=*,-voice,-remote_control

# Disable all, then selectively enable
JCLAW_FEATURES=-*,scheduler

Available flags
───────────────
parallel        — multi-agent / concurrent container runs
web_search      — WebSearch container skill
web_fetch       — WebFetch container skill
host_browser    — host-controlled browser automation
vision          — image/multimodal content processing
voice           — voice message transcription
scheduler       — cron/scheduled task runner
remote_control  — VS Code remote control sessions
compact         — automatic context compaction
ipc             — IPC file watcher (inter-container messaging)
credential_proxy — model credential proxy (disable only for Anthropic direct)
"""
from __future__ import annotations

import os
from typing import FrozenSet

# ── Known flags and their defaults (all on) ──────────────────────────────────

ALL_FLAGS: FrozenSet[str] = frozenset({
    "parallel",
    "web_search",
    "web_fetch",
    "host_browser",
    "vision",
    "voice",
    "scheduler",
    "remote_control",
    "compact",
    "ipc",
    "credential_proxy",
})

# Aliases for human-friendly names
_ALIASES: dict[str, str] = {
    "web": "web_search",
    "browser": "host_browser",
    "img": "vision",
    "image": "vision",
    "cron": "scheduler",
    "tasks": "scheduler",
    "rc": "remote_control",
    "proxy": "credential_proxy",
}


def _resolve(name: str) -> str:
    return _ALIASES.get(name, name)


def _parse_env() -> FrozenSet[str]:
    """Compute the active flag set from JCLAW_FEATURES."""
    raw = os.environ.get("JCLAW_FEATURES", "").strip()
    if not raw:
        return ALL_FLAGS

    active: set[str] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue

        if token == "*":
            active.update(ALL_FLAGS)
        elif token == "-*":
            active.clear()
        elif token.startswith("-"):
            flag = _resolve(token[1:])
            active.discard(flag)
        else:
            flag = _resolve(token)
            active.add(flag)

    return frozenset(active)


# Singleton — parsed once at import time for zero-overhead hot path checks.
_ACTIVE: FrozenSet[str] = _parse_env()


def is_enabled(flag: str) -> bool:
    """Return True if *flag* is active in the current feature set.

    Accepts both canonical names (``web_search``) and aliases (``web``).
    Unknown flags return False so callers can gate on not-yet-registered
    capabilities safely.
    """
    return _resolve(flag) in _ACTIVE


def active_flags() -> FrozenSet[str]:
    """Return the full set of active flag names."""
    return _ACTIVE


def refresh() -> None:
    """Re-parse JCLAW_FEATURES from the environment.

    Call this after modifying os.environ at runtime (e.g. tests, dynamic
    config reload).
    """
    global _ACTIVE  # noqa: PLW0603
    _ACTIVE = _parse_env()
