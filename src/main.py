"""Python entrypoint for J Claw core services.

Current scope:
- Initialize SQLite state
- Start credential proxy for container model access

Run with:
  python -m src.main init-db
  python -m src.main start-proxy --host 127.0.0.1 --port 3001
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from http.server import HTTPServer
from pathlib import Path

# ── Load .env BEFORE any relative imports so module-level config reads work ──
def _preload_env() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

_preload_env()

from .config import AGENT_RUNNER_DIR, CREDENTIAL_PROXY_PORT, NODE_BIN
from .credential_proxy import start_credential_proxy
from .db import init_database
from .feature_flags import ALL_FLAGS, active_flags
from .index import run_orchestrator
from .logger import logger
from .model_registry import load_model_registry
from .profiles import get_profile, list_profiles


def _collect_ts_files(root: Path) -> list[Path]:
    ts_files = sorted(root.rglob("*.ts"))
    excluded_parts = {"node_modules", ".git", "dist", "build"}
    return [p for p in ts_files if not any(part in excluded_parts for part in p.parts)]


def _classify_ts_file(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    if rel.startswith("container/agent-runner/src/"):
        return "agent_runner"
    if rel.startswith(".claude/skills/"):
        return "skills"
    if rel.startswith("src/"):
        return "core_runtime"
    return "other"


def _has_git_repo(root: Path) -> bool:
    if (root / ".git").exists():
        return True
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return True
    except Exception:
        return False


def cmd_init_db(_: argparse.Namespace) -> int:
    init_database()
    logger.info("Database initialized")
    return 0


def cmd_start_proxy(args: argparse.Namespace) -> int:
    server: HTTPServer = start_credential_proxy(port=args.port, host=args.host)
    logger.info("Credential proxy running on %s:%s", args.host, args.port)

    stop = {"value": False}

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("Received signal %s, shutting down proxy", signum)
        stop["value"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not stop["value"]:
        time.sleep(0.25)

    server.shutdown()
    server.server_close()
    logger.info("Credential proxy stopped")
    return 0


def cmd_port_audit(args: argparse.Namespace) -> int:
    root = Path.cwd()
    filtered = _collect_ts_files(root)
    categories: dict[str, list[str]] = {
        "core_runtime": [],
        "agent_runner": [],
        "skills": [],
        "other": [],
    }
    for p in filtered:
        rel = p.relative_to(root).as_posix()
        categories[_classify_ts_file(p, root)].append(rel)

    payload = {
        "ts_remaining": len(filtered),
        "core_runtime_ts": len(categories["core_runtime"]),
        "categories": {
            "agent_runner": len(categories["agent_runner"]),
            "skills": len(categories["skills"]),
            "other": len(categories["other"]),
        },
        "files": {
            "core_runtime": categories["core_runtime"],
            "agent_runner": categories["agent_runner"],
            "skills": categories["skills"],
            "other": categories["other"],
        },
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=True))
        return 0

    print(f"TypeScript files remaining: {payload['ts_remaining']}")
    print(f"Core runtime TypeScript (src): {payload['core_runtime_ts']}")
    print(
        "TS categories: "
        f"agent_runner={payload['categories']['agent_runner']}, "
        f"skills={payload['categories']['skills']}, "
        f"other={payload['categories']['other']}"
    )

    for key in ["core_runtime", "agent_runner", "skills", "other"]:
        for rel in payload["files"][key]:
            print(rel)

    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print("J Claw doctor report")

    def ok(name: str, detail: str) -> None:
        print(f"[OK] {name}: {detail}")

    def warn(name: str, detail: str) -> None:
        print(f"[WARN] {name}: {detail}")

    # Runtime basics
    python_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok("Python", python_ver)

    root = Path.cwd()
    if _has_git_repo(root):
        ok("Repository", "git metadata found")
    else:
        warn("Repository", "No git repo detected (git-dependent features unavailable)")

    node_path = shutil.which(NODE_BIN) or NODE_BIN
    if shutil.which(NODE_BIN):
        ok("Node.js", node_path)
    else:
        warn("Node.js", f"{NODE_BIN} not found in PATH")

    agent_dist = AGENT_RUNNER_DIR / "dist" / "index.js"
    if agent_dist.exists():
        ok("Agent runner", str(agent_dist))
    else:
        warn("Agent runner", "not built — run: jclaw services build")

    # Optional dependencies
    for pkg in ["httpx", "croniter", "playwright.async_api"]:
        try:
            importlib.import_module(pkg)
            ok("Python package", pkg)
        except Exception:
            warn("Python package", f"Missing {pkg}")

    # Channel module import checks
    modules: list[str] = []
    raw = args.channels.strip() if args.channels else ""
    if raw:
        modules = [m.strip() for m in raw.split(",") if m.strip()]
    for mod_name in modules:
        try:
            importlib.import_module(mod_name)
            ok("Channel module", mod_name)
        except Exception as exc:
            warn("Channel module", f"{mod_name} failed to import ({exc})")

    # ── Model registry (direct backend routing) ────────────────────────
    registry = load_model_registry()
    aliases = registry.all_aliases()
    if aliases:
        for alias in sorted(aliases):
            ep = registry.resolve(alias)
            if ep:
                # Probe the backend health endpoint (non-blocking, short timeout)
                try:
                    import httpx as _httpx
                    r = _httpx.get(f"{ep.url}/models", timeout=3.0,
                                   headers={"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {})
                    if r.status_code < 400:
                        ok("Model alias", f"{alias} → {ep.url} ({ep.model}) [HTTP {r.status_code}]")
                    else:
                        warn("Model alias", f"{alias} → {ep.url} reachable but returned {r.status_code}")
                except Exception as exc:
                    warn("Model alias", f"{alias} → {ep.url} not reachable ({exc.__class__.__name__}: {exc})")
    else:
        # Fall back to the old JCLAW_GATEWAY_BASE_URL style
        gateway = __import__("os").environ.get("JCLAW_GATEWAY_BASE_URL", "")
        if gateway:
            warn("Model registry", f"No JCLAW_MODEL_ALIASES; using legacy gateway {gateway}")
        else:
            warn("Model registry", "No JCLAW_MODEL_ALIASES or JCLAW_GATEWAY_BASE_URL set — model routing unconfigured")

    # ── Feature flags ────────────────────────────────────────────────
    active = active_flags()
    disabled = ALL_FLAGS - active
    if disabled:
        warn("Features", f"active={len(active)}/{len(ALL_FLAGS)}, disabled={','.join(sorted(disabled))}")
    else:
        ok("Features", f"all {len(ALL_FLAGS)} flags active")

    # TS migration status
    filtered = _collect_ts_files(root)
    core_runtime = [p for p in filtered if _classify_ts_file(p, root) == "core_runtime"]
    agent_runner = [p for p in filtered if _classify_ts_file(p, root) == "agent_runner"]
    skills = [p for p in filtered if _classify_ts_file(p, root) == "skills"]
    other = [p for p in filtered if _classify_ts_file(p, root) == "other"]
    ok(
        "Migration",
        (
            f"TS remaining={len(filtered)}, core src TS={len(core_runtime)}, "
            f"agent_runner TS={len(agent_runner)}, skills TS={len(skills)}, other TS={len(other)}"
        ),
    )

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.profile:
        profile = get_profile(args.profile)
        if profile is None:
            print(f"Unknown profile: {args.profile}")
            print("Use 'jclaw profiles' to list available profiles.")
            return 1
        for k, v in profile.env.items():
            if k not in __import__("os").environ:
                __import__("os").environ[k] = v

    channel_modules = [m.strip() for m in args.channels.split(",") if m.strip()] if args.channels else []
    asyncio.run(
        run_orchestrator(
            allow_no_channels=args.allow_no_channels,
            channel_modules=channel_modules,
        )
    )
    return 0


def cmd_profiles(_: argparse.Namespace) -> int:
    print("Available runtime profiles:")
    for p in list_profiles():
        print(f"- {p.name}: {p.description}")
    return 0


def cmd_setup_step(args: argparse.Namespace) -> int:
    step_map = {
        "environment": "setup_py.environment",
        "container": "setup_py.container",
        "groups": "setup_py.groups",
        "register": "setup_py.register",
        "mounts": "setup_py.mounts",
        "service": "setup_py.service",
        "verify": "setup_py.verify",
    }
    mod = importlib.import_module(step_map[args.step])
    forwarded = list(args.step_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    mod.run(forwarded)
    return 0


def cmd_agent_runner_parity(_: argparse.Namespace) -> int:
    root = Path.cwd()
    index_path = root / "container" / "agent-runner" / "src" / "index.ts"
    ipc_path = root / "container" / "agent-runner" / "src" / "ipc-mcp-stdio.ts"

    files = {
        "index": index_path,
        "ipc": ipc_path,
    }
    missing_files = [str(path.relative_to(root)) for path in files.values() if not path.exists()]
    if missing_files:
        print(json.dumps({"status": "failed", "missing_files": missing_files}, ensure_ascii=True))
        return 1

    index_text = index_path.read_text(encoding="utf-8")
    ipc_text = ipc_path.read_text(encoding="utf-8")

    required_index_tokens = [
        "OUTPUT_START_MARKER",
        "OUTPUT_END_MARKER",
        "IPC_INPUT_CLOSE_SENTINEL",
        "allowDangerouslySkipPermissions: true",
        "permissionMode: 'bypassPermissions'",
        "resumeSessionAt",
        "TaskStop",
        "TeamCreate",
        "TeamDelete",
        "SendMessage",
    ]
    missing_index_tokens = [tok for tok in required_index_tokens if tok not in index_text]

    required_ipc_tokens = [
        "const MESSAGES_DIR = path.join(IPC_DIR, 'messages')",
        "const TASKS_DIR = path.join(IPC_DIR, 'tasks')",
        "const BROWSER_RESULTS_DIR = path.join(IPC_DIR, 'browser_results')",
        "NANOCLAW_CHAT_JID",
        "NANOCLAW_GROUP_FOLDER",
        "NANOCLAW_IS_MAIN",
    ]
    missing_ipc_tokens = [tok for tok in required_ipc_tokens if tok not in ipc_text]

    found_tools = sorted(set(re.findall(r"server\.tool\(\s*'([^']+)'", ipc_text, flags=re.MULTILINE)))
    expected_tools = sorted(
        [
            "send_message",
            "schedule_task",
            "list_tasks",
            "pause_task",
            "resume_task",
            "cancel_task",
            "update_task",
            "register_group",
            "host_browser_open",
            "host_browser_search_google",
            "host_browser_snapshot",
            "host_browser_click",
            "host_browser_fill",
            "host_browser_press",
            "host_browser_read_text",
            "host_browser_close",
            "download_from_web",
        ]
    )
    missing_tools = [name for name in expected_tools if name not in found_tools]
    extra_tools = [name for name in found_tools if name not in expected_tools]

    payload = {
        "status": "ok" if not (missing_index_tokens or missing_ipc_tokens or missing_tools) else "failed",
        "files": {
            "index": str(index_path.relative_to(root).as_posix()),
            "ipc": str(ipc_path.relative_to(root).as_posix()),
        },
        "missing_index_tokens": missing_index_tokens,
        "missing_ipc_tokens": missing_ipc_tokens,
        "expected_tools": expected_tools,
        "found_tools": found_tools,
        "missing_tools": missing_tools,
        "extra_tools": extra_tools,
    }
    print(json.dumps(payload, ensure_ascii=True))
    return 0 if payload["status"] == "ok" else 1


def cmd_agent_runner_python_prototype(_: argparse.Namespace) -> int:
    root = Path.cwd()
    proto_path = root / "container" / "agent-runner" / "python" / "runner.py"
    if not proto_path.exists():
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "prototype_missing",
                    "file": str(proto_path.relative_to(root).as_posix()),
                },
                ensure_ascii=True,
            )
        )
        return 1

    sample_input = {
        "prompt": "smoke test",
        "groupFolder": "main",
        "chatJid": "test@g.us",
        "isMain": True,
    }

    proc = subprocess.run(
        [sys.executable, str(proto_path)],
        input=json.dumps(sample_input),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    stdout = proc.stdout or ""
    marker_start = "---JCLAW_OUTPUT_START---"
    marker_end = "---JCLAW_OUTPUT_END---"
    has_markers = marker_start in stdout and marker_end in stdout

    payload_obj: dict[str, object] | None = None
    if has_markers:
        try:
            start_idx = stdout.index(marker_start) + len(marker_start)
            end_idx = stdout.index(marker_end, start_idx)
            body = stdout[start_idx:end_idx].strip()
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                payload_obj = parsed
        except Exception:
            payload_obj = None

    out = {
        "status": "ok" if (proc.returncode == 0 and has_markers and isinstance(payload_obj, dict)) else "failed",
        "prototype": str(proto_path.relative_to(root).as_posix()),
        "returncode": proc.returncode,
        "has_markers": has_markers,
        "payload": payload_obj,
        "stderr": (proc.stderr or "").strip()[:500],
    }
    print(json.dumps(out, ensure_ascii=True))
    return 0 if out["status"] == "ok" else 1


def cmd_provider(args: argparse.Namespace) -> int:
    """Interactive provider/model alias manager.

    Reads and writes the JCLAW_MODEL_ALIASES section of .env so users never
    have to hand-craft JSON.  Modelled on openclaude's /provider workflow.
    """
    import json as _json
    root = Path.cwd()
    env_path = root / ".env"

    def _read_env() -> dict[str, str]:
        lines: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    lines[k.strip()] = v.strip()
        return lines

    def _write_env(kv: dict[str, str]) -> None:
        """Merge kv into .env, preserving existing lines and comments."""
        existing_lines: list[str] = []
        updated_keys: set[str] = set()
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k = line.partition("=")[0].strip()
                    if k in kv:
                        existing_lines.append(f"{k}={kv[k]}")
                        updated_keys.add(k)
                        continue
                existing_lines.append(line)
        for k, v in kv.items():
            if k not in updated_keys:
                existing_lines.append(f"{k}={v}")
        env_path.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")

    def _load_aliases() -> dict[str, dict]:
        env_kv = _read_env()
        raw = env_kv.get("JCLAW_MODEL_ALIASES", "")
        if not raw:
            return {}
        try:
            return _json.loads(raw)
        except _json.JSONDecodeError:
            return {}

    def _save_aliases(aliases: dict[str, dict]) -> None:
        _write_env({"JCLAW_MODEL_ALIASES": _json.dumps(aliases, separators=(",", ":"))})

    sub = args.provider_cmd

    if sub == "list":
        registry = load_model_registry()
        alias_list = registry.all_aliases()
        if not alias_list:
            print("No model aliases configured. Run:")
            print("  python -m src.main provider add --alias jclaw-main --url http://127.0.0.1:5002/v1 --model mymodel.gguf")
            return 0
        print(f"Configured model aliases ({len(alias_list)}):")
        for name in sorted(alias_list):
            ep = registry.resolve(name)
            if ep:
                print(f"  {name}")
                print(f"    url   : {ep.url}")
                print(f"    model : {ep.model}")
                print(f"    key   : {'(set)' if ep.api_key and ep.api_key != 'dummy-key' else '(not set)'}")
        return 0

    if sub == "add":
        if not args.alias or not args.url or not args.model:
            print("Error: --alias, --url, and --model are required.")
            return 1
        aliases = _load_aliases()
        aliases[args.alias] = {
            "url": args.url.rstrip("/"),
            "model": args.model,
            "key": args.key or "dummy-key",
        }
        _save_aliases(aliases)
        print(f"Saved alias '{args.alias}' → {args.url} (model: {args.model})")
        # Also set JCLAW_MODEL so this becomes the default if it's the first alias
        env_kv = _read_env()
        if "JCLAW_MODEL" not in env_kv:
            _write_env({"JCLAW_MODEL": args.alias})
            print(f"Set JCLAW_MODEL={args.alias} as default")
        print("Restart the credential proxy for changes to take effect.")
        return 0

    if sub == "remove":
        if not args.alias:
            print("Error: --alias is required.")
            return 1
        aliases = _load_aliases()
        if args.alias not in aliases:
            print(f"Alias '{args.alias}' not found.")
            return 1
        del aliases[args.alias]
        _save_aliases(aliases)
        print(f"Removed alias '{args.alias}'.")
        return 0

    if sub == "test":
        alias = args.alias
        registry = load_model_registry()
        ep = registry.resolve(alias) if alias else None
        candidates = [alias] if alias else sorted(registry.all_aliases())
        if not candidates:
            print("No aliases configured.")
            return 1
        import httpx as _httpx
        exit_code = 0
        for name in candidates:
            ep = registry.resolve(name)
            if not ep:
                print(f"[MISS] {name}: not in registry")
                exit_code = 1
                continue
            try:
                r = _httpx.get(f"{ep.url}/models", timeout=5.0,
                               headers={"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {})
                if r.status_code < 400:
                    print(f"[OK]   {name}: {ep.url} → HTTP {r.status_code}")
                else:
                    print(f"[FAIL] {name}: {ep.url} → HTTP {r.status_code}")
                    exit_code = 1
            except Exception as exc:
                print(f"[FAIL] {name}: {ep.url} → {exc.__class__.__name__}: {exc}")
                exit_code = 1
        return exit_code

    print(f"Unknown provider subcommand: {sub}")
    return 1


# ── CLI / prompt / UI command handlers ───────────────────────────────────────

def cmd_chat(args: argparse.Namespace) -> int:
    from .cli import run_chat
    return run_chat(
        group_name=args.group,
        pipe_mode=getattr(args, "pipe", False),
        session_id=args.session or None,
    )


def cmd_prompt(args: argparse.Namespace) -> int:
    text: str = args.text or sys.stdin.read().strip()
    if not text:
        print("Error: no prompt text provided", file=sys.stderr)
        return 1
    # Allow CLI-level timeout override for single-shot prompts
    timeout_sec = getattr(args, "timeout", 0)
    if timeout_sec and timeout_sec > 0:
        os.environ["CONTAINER_TIMEOUT"] = str(timeout_sec * 1000)
        os.environ["IDLE_TIMEOUT"] = str(timeout_sec * 1000)
    from .cli import run_single_prompt
    return run_single_prompt(text, group_name=args.group, session_id=args.session or None)


def cmd_ui(args: argparse.Namespace) -> int:
    if getattr(args, "open", False):
        import webbrowser
        import threading
        url = f"http://{args.host}:{args.port}"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        print(f"Opening {url} in browser…")
    from .ui import start_ui_server
    start_ui_server(port=args.port, host=args.host)
    return 0


# ── services command handlers ─────────────────────────────────────────────────

def cmd_services(args: argparse.Namespace) -> int:
    from .services import (
        cmd_services_build,
        cmd_services_start,
        cmd_services_status,
        cmd_services_stop,
        cmd_services_test,
    )
    sub = getattr(args, "services_sub", "status")
    if sub == "status":
        return cmd_services_status()
    if sub == "start":
        return cmd_services_start(
            main_only=getattr(args, "main_only", False),
            no_proxy=getattr(args, "no_proxy", False),
            wait=getattr(args, "wait", 120),
        )
    if sub == "stop":
        return cmd_services_stop()
    if sub == "test":
        return cmd_services_test(
            prompt=getattr(args, "prompt_text", "Reply with exactly: JCLAW_OK"),
            no_think=not getattr(args, "thinking", False),
        )
    if sub == "build":
        return cmd_services_build(no_cache=getattr(args, "no_cache", False))
    print(f"Unknown services subcommand: {sub}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jclaw", description="J Claw Python runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    init_db = sub.add_parser("init-db", help="Initialize SQLite database schema")
    init_db.set_defaults(func=cmd_init_db)

    start_proxy = sub.add_parser("start-proxy", help="Start credential proxy")
    start_proxy.add_argument("--host", default="127.0.0.1", help="Bind host")
    start_proxy.add_argument("--port", type=int, default=CREDENTIAL_PROXY_PORT, help="Bind port")
    start_proxy.set_defaults(func=cmd_start_proxy)

    port_audit = sub.add_parser(
        "port-audit",
        help="Report remaining TypeScript files in the workspace",
    )
    port_audit.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report",
    )
    port_audit.set_defaults(func=cmd_port_audit)

    doctor = sub.add_parser("doctor", help="Run environment and migration diagnostics")
    doctor.add_argument(
        "--channels",
        default="",
        help="Comma-separated channel modules to import-check (optional)",
    )
    doctor.set_defaults(func=cmd_doctor)

    run = sub.add_parser("run", help="Start the full J Claw Python orchestrator")
    run.add_argument(
        "--allow-no-channels",
        action="store_true",
        help="Allow startup without connected channels",
    )
    run.add_argument(
        "--channels",
        default="",
        help="Comma-separated channel module import paths",
    )
    run.add_argument(
        "--profile",
        default="",
        help="Runtime profile preset (see 'jclaw profiles')",
    )
    run.set_defaults(func=cmd_run)

    profiles = sub.add_parser("profiles", help="List built-in runtime profile presets")
    profiles.set_defaults(func=cmd_profiles)

    setup_step = sub.add_parser("setup-step", help="Run Python setup step")
    setup_step.add_argument(
        "step",
        choices=["environment", "container", "groups", "register", "mounts", "service", "verify"],
    )
    setup_step.add_argument("step_args", nargs=argparse.REMAINDER)
    setup_step.set_defaults(func=cmd_setup_step)

    parity = sub.add_parser(
        "agent-runner-parity",
        help="Validate protocol/tool parity for the remaining agent-runner TypeScript files",
    )
    parity.set_defaults(func=cmd_agent_runner_parity)

    proto = sub.add_parser(
        "agent-runner-python-prototype",
        help="Smoke-test Python sidecar prototype output contract without changing runtime wiring",
    )
    proto.set_defaults(func=cmd_agent_runner_python_prototype)

    # ── provider ───────────────────────────────────────────────────
    provider = sub.add_parser(
        "provider",
        help="Manage model provider aliases (list, add, remove, test)",
    )
    provider_sub = provider.add_subparsers(dest="provider_cmd", required=True)

    p_list = provider_sub.add_parser("list", help="List configured model aliases")
    p_list.set_defaults(func=cmd_provider)

    p_add = provider_sub.add_parser("add", help="Add or update a model alias")
    p_add.add_argument("--alias", required=True, help="Logical alias name (e.g. jclaw-main)")
    p_add.add_argument("--url", required=True, help="Backend base URL (e.g. http://127.0.0.1:5002/v1)")
    p_add.add_argument("--model", required=True, help="Model filename/ID served by the backend")
    p_add.add_argument("--key", default="dummy-key", help="API key (default: dummy-key for local servers)")
    p_add.set_defaults(func=cmd_provider)

    p_remove = provider_sub.add_parser("remove", help="Remove a model alias")
    p_remove.add_argument("--alias", required=True, help="Alias name to remove")
    p_remove.set_defaults(func=cmd_provider)

    p_test = provider_sub.add_parser("test", help="Probe backend(s) for health")
    p_test.add_argument("--alias", default="", help="Specific alias to test (default: all)")
    p_test.set_defaults(func=cmd_provider)

    # ── chat / prompt ──────────────────────────────────────────────────────────
    chat = sub.add_parser("chat", help="Interactive rich terminal REPL")
    chat.add_argument("--group", default="", help="Target group name or folder (default: main)")
    chat.add_argument("--session", default="", help="Resume an existing session ID")
    chat.add_argument("--pipe", action="store_true", help="Read a single prompt from stdin then exit")
    chat.set_defaults(func=cmd_chat)

    prompt_cmd = sub.add_parser("prompt", help="Send a single prompt and exit")
    prompt_cmd.add_argument("text", nargs="?", default="", help="Prompt text (reads stdin if omitted)")
    prompt_cmd.add_argument("--group", default="", help="Target group name or folder")
    prompt_cmd.add_argument("--session", default="", help="Session ID to use")
    prompt_cmd.add_argument("--timeout", type=int, default=0, help="Max seconds to wait for response (0 = use env default)")
    prompt_cmd.set_defaults(func=cmd_prompt)

    # ── ui ─────────────────────────────────────────────────────────────────────
    ui_cmd = sub.add_parser("ui", help="Start the web dashboard UI")
    ui_cmd.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    ui_cmd.add_argument("--port", type=int, default=7842, help="Port (default 7842)")
    ui_cmd.add_argument("--open", action="store_true", help="Open browser automatically")
    ui_cmd.set_defaults(func=cmd_ui)

    # ── services ───────────────────────────────────────────────────────────────
    svc = sub.add_parser("services", help="Manage local services (llama-server, Docker, proxy)")
    svc_sub = svc.add_subparsers(dest="services_sub")
    svc_sub.required = True

    svc_status = svc_sub.add_parser("status", help="Show status of all local services")
    svc_status.set_defaults(func=cmd_services)

    svc_start = svc_sub.add_parser("start", help="Start llama-server(s) and credential proxy")
    svc_start.add_argument("--main-only", action="store_true", dest="main_only",
                           help="Only start the main model (skip worker)")
    svc_start.add_argument("--no-proxy", action="store_true", dest="no_proxy",
                           help="Skip starting the credential proxy")
    svc_start.add_argument("--wait", type=int, default=120,
                           help="Seconds to wait for llama-server to become ready (default 120)")
    svc_start.set_defaults(func=cmd_services)

    svc_stop = svc_sub.add_parser("stop", help="Stop llama-server processes and proxy")
    svc_stop.set_defaults(func=cmd_services)

    svc_test = svc_sub.add_parser("test", help="Run full smoke test (model + Docker + container)")
    svc_test.add_argument("--prompt", dest="prompt_text", default="Reply with exactly: JCLAW_OK",
                          help="Test prompt text")
    svc_test.add_argument("--thinking", action="store_true",
                          help="Enable thinking mode (do NOT add /no_think prefix)")
    svc_test.set_defaults(func=cmd_services)

    svc_build = svc_sub.add_parser("build", help="Rebuild the agent Docker image")
    svc_build.add_argument("--no-cache", action="store_true", dest="no_cache",
                           help="Pass --no-cache to docker build")
    svc_build.set_defaults(func=cmd_services)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = argv if argv is not None else sys.argv[1:]

    # setup-step accepts arbitrary option-like args for the delegated step.
    # Handle it early so users do not need an extra '--' separator.
    if len(raw_argv) >= 2 and raw_argv[0] == "setup-step":
        class _SetupArgs:
            def __init__(self, step: str, step_args: list[str]) -> None:
                self.step = step
                self.step_args = step_args

        return cmd_setup_step(_SetupArgs(raw_argv[1], raw_argv[2:]))

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
