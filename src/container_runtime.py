"""No-op container runtime stub — J Claw runs agents directly via Node.js (no Docker)."""
from __future__ import annotations

import os

PROXY_BIND_HOST = os.environ.get("CREDENTIAL_PROXY_HOST", "127.0.0.1")


def ensure_container_runtime_running() -> None:
    """No-op: Docker no longer required."""


def cleanup_orphans() -> None:
    """No-op: no containers to clean up."""
