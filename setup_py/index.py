"""Python setup step runner.

Usage:
    python -m setup_py.index --step <environment|container|groups|register|mounts|service|verify>
"""
from __future__ import annotations

import argparse
import importlib

from src.logger import logger

from .status import emit_status

STEPS = {
    "environment": "setup_py.environment",
    "container": "setup_py.container",
    "groups": "setup_py.groups",
    "register": "setup_py.register",
    "mounts": "setup_py.mounts",
    "service": "setup_py.service",
    "verify": "setup_py.verify",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jclaw-setup")
    parser.add_argument("--step", required=True, choices=sorted(STEPS.keys()))
    parser.add_argument("step_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    try:
        mod = importlib.import_module(STEPS[args.step])
        mod.run(args.step_args)
        return 0
    except Exception as exc:
        logger.error("Setup step failed: %s", exc)
        emit_status(args.step.upper(), {"STATUS": "failed", "ERROR": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
