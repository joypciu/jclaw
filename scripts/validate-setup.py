"""
J Claw — Setup Validator
========================
Validates that the environment is correctly configured for J Claw.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def check_env_file() -> dict:
    result = {"ok": False, "errors": [], "info": {}}
    env_path = Path(".env")
    if not env_path.exists():
        result["errors"].append(".env file not found. Run scripts/setup-local-windows.ps1 first.")
        return result
    result["info"]["env_path"] = str(env_path.resolve())
    content = env_path.read_text(encoding="utf-8")
    required = ["JCLAW_MODEL", "JCLAW_GATEWAY_BASE_URL"]
    missing = []
    for key in required:
        if key not in content:
            missing.append(key)
    if missing:
        result["errors"].append(f"Missing required vars in .env: {', '.join(missing)}")
    else:
        result["ok"] = True
    return result


def check_node() -> dict:
    result = {"ok": False, "errors": [], "info": {}}
    import shutil
    node = shutil.which("node")
    if not node:
        result["errors"].append("Node.js not found in PATH. Install Node 20+.")
    else:
        result["info"]["node"] = node
        result["ok"] = True
    return result


def check_docker() -> dict:
    result = {"ok": False, "errors": [], "info": {}}
    import shutil
    docker = shutil.which("docker")
    if not docker:
        result["errors"].append("Docker not found in PATH. Install Docker Desktop + WSL2.")
    else:
        result["info"]["docker"] = docker
        result["ok"] = True
    return result


def main():
    print("=" * 60)
    print(" J Claw — Setup Validator")
    print("=" * 60)

    checks = [
        ("Environment file (.env)", check_env_file),
        ("Node.js", check_node),
        ("Docker", check_docker),
    ]

    all_ok = True
    for name, fn in checks:
        print(f"\n[{name}]")
        res = fn()
        for k, v in res.get("info", {}).items():
            print(f"  {k}: {v}")
        for e in res["errors"]:
            print(f"  ✗ {e}")
            all_ok = False
        if res["ok"]:
            print("  ✓ OK")

    print("\n" + "=" * 60)
    if all_ok:
        print(" All checks passed — J Claw should be ready to run!")
        print("   python -m src.main")
    else:
        print(" Some checks failed. Fix the issues above and re-run.")
    print("=" * 60)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
