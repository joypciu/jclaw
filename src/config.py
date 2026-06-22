"""Configuration constants from environment variables."""
import os
import re
import shutil
from pathlib import Path

from .env import read_env_file

# Read config values from .env (falls back to os.environ).
# Secrets (API keys, tokens) are NOT read here.
_env = read_env_file([
    "ASSISTANT_NAME",
    "ASSISTANT_HAS_OWN_NUMBER",
    "JCLAW_MODEL",
    "JCLAW_WORKER_MODEL",
    "JCLAW_FALLBACK_MODEL",
    "JCLAW_USE_WORKER_MODEL_FOR_SCHEDULED",
])

def _get(key: str, default: str = "") -> str:
    return os.environ.get(key) or _env.get(key, default)

PRODUCT_NAME = "J Claw"
PRODUCT_SLUG = "jclaw"

ASSISTANT_NAME = _get("ASSISTANT_NAME", "JClaw")
ASSISTANT_HAS_OWN_NUMBER = _get("ASSISTANT_HAS_OWN_NUMBER", "false").lower() == "true"
JCLAW_MODEL: str | None = _get("JCLAW_MODEL") or None
JCLAW_WORKER_MODEL: str | None = _get("JCLAW_WORKER_MODEL") or None
JCLAW_FALLBACK_MODEL: str | None = _get("JCLAW_FALLBACK_MODEL") or None
JCLAW_USE_WORKER_MODEL_FOR_SCHEDULED = _get("JCLAW_USE_WORKER_MODEL_FOR_SCHEDULED", "true").lower() != "false"

POLL_INTERVAL = 2.0        # seconds
SCHEDULER_POLL_INTERVAL = 60.0  # seconds
IPC_POLL_INTERVAL = 1.0    # seconds

# Absolute paths — resolve relative to the source module, not cwd
PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOME_DIR = Path(os.environ.get("HOME") or Path.home())

MOUNT_ALLOWLIST_PATH = HOME_DIR / ".config" / PRODUCT_SLUG / "mount-allowlist.json"
SENDER_ALLOWLIST_PATH = HOME_DIR / ".config" / PRODUCT_SLUG / "sender-allowlist.json"
STORE_DIR = PROJECT_ROOT / "store"
GROUPS_DIR = PROJECT_ROOT / "groups"
DATA_DIR = PROJECT_ROOT / "data"

# Agent runner (no Docker — runs node directly)
AGENT_RUNNER_DIR = PROJECT_ROOT / "container" / "agent-runner"
NODE_BIN = os.environ.get("NODE_BIN") or shutil.which("node") or "node"

CONTAINER_IMAGE = os.environ.get("CONTAINER_IMAGE", "nanoclaw-agent:latest")  # kept for compat
CONTAINER_TIMEOUT = int(os.environ.get("CONTAINER_TIMEOUT", "1800000"))
CONTAINER_MAX_OUTPUT_SIZE = int(os.environ.get("CONTAINER_MAX_OUTPUT_SIZE", str(10 * 1024 * 1024)))
CREDENTIAL_PROXY_PORT = int(os.environ.get("CREDENTIAL_PROXY_PORT", "3001"))
IDLE_TIMEOUT = int(os.environ.get("IDLE_TIMEOUT", "1800000"))
MAX_CONCURRENT_CONTAINERS = max(1, int(os.environ.get("MAX_CONCURRENT_CONTAINERS", "5") or "5"))

TIMEZONE = os.environ.get("TZ") or "UTC"
try:
    import time as _t
    TIMEZONE = _t.tzname[0] if not os.environ.get("TZ") else TIMEZONE
except Exception:
    pass

def _escape_regex(s: str) -> str:
    return re.escape(s)

TRIGGER_PATTERN = re.compile(r"^@" + _escape_regex(ASSISTANT_NAME) + r"\b", re.IGNORECASE)
