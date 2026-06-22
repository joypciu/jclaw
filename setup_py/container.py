"""Setup step: container build and smoke test."""
from __future__ import annotations

import subprocess
from pathlib import Path

from src.logger import logger

from .platform import command_exists, docker_running
from .status import emit_status


def _arg_value(args: list[str], key: str, default: str = "") -> str:
    for i, token in enumerate(args):
        if token == key and i + 1 < len(args):
            return args[i + 1]
    return default


def run(args: list[str]) -> None:
    runtime = _arg_value(args, "--runtime")
    image = "jclaw-agent:latest"

    if runtime not in {"docker", "apple-container"}:
        emit_status(
            "SETUP_CONTAINER",
            {
                "RUNTIME": runtime or "unknown",
                "IMAGE": image,
                "BUILD_OK": False,
                "TEST_OK": False,
                "STATUS": "failed",
                "ERROR": "unknown_runtime",
            },
        )
        raise SystemExit(4)

    if runtime == "docker":
        if not command_exists("docker") or not docker_running():
            emit_status(
                "SETUP_CONTAINER",
                {
                    "RUNTIME": runtime,
                    "IMAGE": image,
                    "BUILD_OK": False,
                    "TEST_OK": False,
                    "STATUS": "failed",
                    "ERROR": "runtime_not_available",
                },
            )
            raise SystemExit(2)
        build_base = ["docker", "build"]
        run_base = ["docker", "run"]
    else:
        if not command_exists("container"):
            emit_status(
                "SETUP_CONTAINER",
                {
                    "RUNTIME": runtime,
                    "IMAGE": image,
                    "BUILD_OK": False,
                    "TEST_OK": False,
                    "STATUS": "failed",
                    "ERROR": "runtime_not_available",
                },
            )
            raise SystemExit(2)
        build_base = ["container", "build"]
        run_base = ["container", "run"]

    project_root = Path.cwd()
    container_dir = project_root / "container"

    build_ok = False
    test_ok = False

    try:
        logger.info("Building container image with %s", runtime)
        subprocess.run(
            [*build_base, "-t", image, "."],
            cwd=str(container_dir),
            check=True,
            capture_output=True,
            text=True,
            timeout=900,
        )
        build_ok = True
    except Exception as exc:
        logger.error("Container build failed: %s", exc)

    if build_ok:
        try:
            logger.info("Smoke-testing container image")
            out = subprocess.run(
                [*run_base, "--rm", "--entrypoint", "/bin/echo", image, "Container OK"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            test_ok = "Container OK" in out.stdout
        except Exception as exc:
            logger.error("Container test failed: %s", exc)

    status = "success" if (build_ok and test_ok) else "failed"
    emit_status(
        "SETUP_CONTAINER",
        {
            "RUNTIME": runtime,
            "IMAGE": image,
            "BUILD_OK": build_ok,
            "TEST_OK": test_ok,
            "STATUS": status,
        },
    )
    if status != "success":
        raise SystemExit(1)
