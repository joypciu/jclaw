"""
Parse the .env file and return values for requested keys.
Does NOT load into os.environ — callers decide what to do with values.
Keeps secrets out of the environment so they don't leak to subprocesses.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def read_env_file(keys: list[str]) -> dict[str, str]:
    # Search .env in: 1) cwd, 2) project root (relative to this file)
    candidates = [
        Path(os.getcwd()) / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    env_file = None
    for candidate in candidates:
        if candidate.exists():
            env_file = candidate
            break
    if env_file is None:
        logger.debug(".env file not found, using defaults")
        return {}
    try:
        content = env_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.debug(f".env file unreadable: {e}")
        return {}

    wanted = set(keys)
    result: dict[str, str] = {}

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        eq = stripped.find("=")
        if eq == -1:
            continue
        key = stripped[:eq].strip()
        if key not in wanted:
            continue
        value = stripped[eq + 1:].strip()
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if value:
            result[key] = value

    return result
