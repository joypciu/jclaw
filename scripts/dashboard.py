"""
J Claw — Status Dashboard
=========================
Pretty terminal dashboard for monitoring J Claw and local model status.

Usage:
    python scripts/dashboard.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.env import read_env_file

# ── Colors ─────────────────────────────────────────────────────────────────
class C:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"


def box(title: str, lines: list[str], width: int = 50):
    """Print a nice box."""
    print(f"\n{C.BOLD}┌{'─' * (width - 2)}┐{C.END}")
    print(f"{C.BOLD}│{C.CYAN} {title:<{width-3}}{C.END}{C.BOLD}│{C.END}")
    print(f"{C.BOLD}├{'─' * (width - 2)}┤{C.END}")
    for line in lines:
        # Strip existing color codes for width calculation
        plain = line.replace(C.BOLD, "").replace(C.END, "").replace(C.GREEN, "").replace(C.RED, "").replace(C.YELLOW, "").replace(C.BLUE, "").replace(C.CYAN, "")
        pad = width - 3 - len(plain)
        print(f"{C.BOLD}│{C.END} {line}{' ' * pad}{C.BOLD}│{C.END}")
    print(f"{C.BOLD}└{'─' * (width - 2)}┘{C.END}")


def check_local_llm(url: str, key: str = "dummy-key") -> dict:
    """Check local LLM endpoint."""
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read())
            return {"ok": True, "models": [m.get("id", "?") for m in data.get("data", [])]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_docker() -> dict:
    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "error": "Not in PATH"}
    try:
        result = shutil.which("docker")
        return {"ok": True, "path": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_node() -> dict:
    node = shutil.which("node")
    if not node:
        return {"ok": False, "error": "Not in PATH"}
    try:
        import subprocess
        result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5)
        return {"ok": True, "version": result.stdout.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_env() -> dict:
    env = read_env_file([
        "JCLAW_MODEL", "JCLAW_GATEWAY_BASE_URL",
        "LLAMACPP_BASE_URL", "ANTHROPIC_API_KEY",
    ])
    return {"ok": bool(env.get("JCLAW_MODEL")), "vars": env}


def main():
    os.system("cls" if os.name == "nt" else "clear")

    print(f"\n{C.BOLD}{C.CYAN}╔══════════════════════════════════════════════════════════════╗{C.END}")
    print(f"{C.BOLD}{C.CYAN}║{C.END}           {C.BOLD}J Claw Status Dashboard{C.END}                       {C.BOLD}{C.CYAN}║{C.END}")
    print(f"{C.BOLD}{C.CYAN}╚══════════════════════════════════════════════════════════════╝{C.END}")

    # Environment
    env_info = check_env()
    env_lines = []
    if env_info["ok"]:
        env_lines.append(f"{C.GREEN}✓{C.END} .env loaded")
        for k, v in env_info["vars"].items():
            display = v if "key" not in k.lower() else "***"
            env_lines.append(f"  {C.BLUE}{k}:{C.END} {display}")
    else:
        env_lines.append(f"{C.YELLOW}⚠ No .env or missing JCLAW_MODEL{C.END}")
    box("Configuration", env_lines)

    # Dependencies
    docker = check_docker()
    node = check_node()
    dep_lines = [
        f"{C.GREEN}✓{C.END} Python {sys.version.split()[0]}" if sys.version_info >= (3, 11) else f"{C.RED}✗{C.END} Python {sys.version.split()[0]} (need 3.11+)",
        f"{C.GREEN}✓{C.END} Node.js {node['version']}" if node["ok"] else f"{C.RED}✗{C.END} Node.js: {node['error']}",
        f"{C.GREEN}✓{C.END} Docker: {docker['path']}" if docker["ok"] else f"{C.YELLOW}⚠{C.END} Docker: {docker['error']}",
    ]
    box("Dependencies", dep_lines)

    # Local LLM
    base_urls = [
        os.environ.get("LLAMACPP_BASE_URL", "http://127.0.0.1:8080/v1"),
        os.environ.get("JCLAW_GATEWAY_BASE_URL", ""),
        "http://127.0.0.1:4000/v1",
    ]
    llm_lines = []
    found = False
    for url in base_urls:
        if not url:
            continue
        result = check_local_llm(url)
        if result["ok"]:
            found = True
            llm_lines.append(f"{C.GREEN}✓{C.END} {url}")
            for m in result["models"]:
                llm_lines.append(f"  {C.CYAN}▸{C.END} {m}")
        else:
            llm_lines.append(f"{C.RED}✗{C.END} {url} ({result['error'][:30]})")
    if not found:
        llm_lines.append("")
        llm_lines.append(f"{C.YELLOW}No local LLM detected.{C.END}")
        llm_lines.append(f"Start one with: scripts/start-kobold-proxy.ps1")
    box("Local LLM", llm_lines)

    # Shared registry
    reg_path = Path("P:/local_ai_agents/.shared_model_registry.json")
    reg_lines = []
    if reg_path.exists():
        try:
            data = json.loads(reg_path.read_text())
            servers = data.get("servers", [])
            if servers:
                for s in servers:
                    age = int(time.time() - s.get("last_seen", 0))
                    status = f"{C.GREEN}●{C.END}" if age < 60 else f"{C.YELLOW}●{C.END}"
                    reg_lines.append(f"{status} Port {s['port']}: {Path(s['model_path']).name}")
                    reg_lines.append(f"  Started by: {s.get('started_by', '?')}")
            else:
                reg_lines.append(f"{C.YELLOW}No registered servers{C.END}")
        except Exception as e:
            reg_lines.append(f"{C.RED}Error reading registry: {e}{C.END}")
    else:
        reg_lines.append(f"{C.YELLOW}No shared registry yet{C.END}")
    box("Shared Model Registry", reg_lines)

    # Actions
    print(f"\n{C.BOLD}Quick Actions:{C.END}")
    print(f"  {C.CYAN}1.{C.END} python scripts/validate-setup.py")
    print(f"  {C.CYAN}2.{C.END} python scripts/check-local-llm.py")
    print(f"  {C.CYAN}3.{C.END} python -m src.main")
    print(f"  {C.CYAN}4.{C.END} python ../integration_smoke_test.py")
    print()


if __name__ == "__main__":
    main()
