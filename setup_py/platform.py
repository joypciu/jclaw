"""Cross-platform setup helpers."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Literal

Platform = Literal["macos", "linux", "windows", "unknown"]
ServiceManager = Literal["launchd", "systemd", "none"]


def get_platform() -> Platform:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "linux":
        return "linux"
    if s == "windows":
        return "windows"
    return "unknown"


def is_wsl() -> bool:
    if get_platform() != "linux":
        return False
    try:
        txt = open("/proc/version", "r", encoding="utf-8").read().lower()
        return "microsoft" in txt or "wsl" in txt
    except Exception:
        return False


def is_root() -> bool:
    try:
        getuid = getattr(os, "getuid", None)
        if getuid is None:
            return False
        return bool(getuid() == 0)
    except Exception:
        return False


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def has_systemd() -> bool:
    if get_platform() != "linux":
        return False
    try:
        init = open("/proc/1/comm", "r", encoding="utf-8").read().strip()
        return init == "systemd"
    except Exception:
        return False


def get_service_manager() -> ServiceManager:
    p = get_platform()
    if p == "macos":
        return "launchd"
    if p == "linux" and has_systemd():
        return "systemd"
    return "none"


def docker_running() -> bool:
    if not command_exists("docker"):
        return False
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True, text=True, timeout=10)
        return True
    except Exception:
        return False
