"""Setup step: service manager bootstrap status and helper generation."""
from __future__ import annotations

import os
from pathlib import Path

from .platform import get_platform, get_service_manager
from .status import emit_status


def _write_linux_wrapper(project_root: Path) -> Path:
    wrapper = project_root / "start-nanoclaw.sh"
    pid_file = project_root / "nanoclaw.pid"
    log_file = project_root / "logs" / "nanoclaw.log"
    err_file = project_root / "logs" / "nanoclaw.error.log"
    wrapper.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                "set -euo pipefail",
                f"cd {project_root}",
                f"if [ -f {pid_file} ]; then",
                f"  OLD_PID=$(cat {pid_file} 2>/dev/null || echo '')",
                "  if [ -n \"$OLD_PID\" ] && kill -0 \"$OLD_PID\" 2>/dev/null; then",
                "    kill \"$OLD_PID\" 2>/dev/null || true",
                "  fi",
                "fi",
                f"nohup python -m src.main run >> {log_file} 2>> {err_file} &",
                f"echo $! > {pid_file}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(wrapper, 0o755)
    except Exception:
        pass
    return wrapper


def run(_args: list[str]) -> None:
    project_root = Path.cwd()
    (project_root / "logs").mkdir(parents=True, exist_ok=True)

    platform_name = get_platform()
    service_manager = get_service_manager()

    if platform_name == "macos":
        emit_status(
            "SETUP_SERVICE",
            {
                "SERVICE_TYPE": "launchd",
                "STATUS": "success",
                "NEXT": "Create launch agent plist pointing to 'python -m src.main run'",
            },
        )
        return

    if platform_name == "linux" and service_manager == "systemd":
        emit_status(
            "SETUP_SERVICE",
            {
                "SERVICE_TYPE": "systemd-user",
                "STATUS": "success",
                "NEXT": "Create user unit for 'python -m src.main run' and enable it",
            },
        )
        return

    if platform_name == "linux":
        wrapper = _write_linux_wrapper(project_root)
        emit_status(
            "SETUP_SERVICE",
            {
                "SERVICE_TYPE": "nohup",
                "WRAPPER_PATH": str(wrapper),
                "STATUS": "success",
            },
        )
        return

    if platform_name == "windows":
        emit_status(
            "SETUP_SERVICE",
            {
                "SERVICE_TYPE": "windows-task-scheduler",
                "STATUS": "success",
                "NEXT": "Create a Task Scheduler entry that runs 'python -m src.main run' at logon",
            },
        )
        return

    emit_status(
        "SETUP_SERVICE",
        {
            "SERVICE_TYPE": "unknown",
            "STATUS": "failed",
            "ERROR": "unsupported_platform",
        },
    )
    raise SystemExit(1)
