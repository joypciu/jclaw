"""Local service management for J Claw.

Manages the prerequisite processes needed to run jclaw locally:
  1. llama-server (main model)     — port from JCLAW_LLAMA_MAIN_PORT (default 5002)
  2. llama-server (worker model)   — port from JCLAW_LLAMA_WORKER_PORT (default 5003)
  3. Credential proxy              — started automatically by `jclaw services start`
  4. Agent runner (Node.js build)  — built by `jclaw services build`

Commands
--------
  jclaw services status   # show green/red status for every service
  jclaw services start    # start llama-server(s) + credential proxy
  jclaw services stop     # kill llama-server processes by port
  jclaw services test     # smoke test (model response + direct agent run)
  jclaw services build    # build the agent-runner (npm run build)

Required .env vars for start
----------------------------
  JCLAW_LLAMA_SERVER_PATH   path to llama-server.exe  (auto-searched if absent)
  JCLAW_MAIN_MODEL_PATH     path to main .gguf file
  JCLAW_WORKER_MODEL_PATH   path to worker .gguf file (optional; shares main if absent)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# -- Rich soft-dep
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    _RICH = True
except ImportError:
    _RICH = False

_console = None


def _con() -> object:
    global _console
    if _console is None:
        if _RICH:
            from rich.console import Console
            _console = Console()
        else:
            class _F:
                def print(self, *a, **k):
                    print(*[str(x) for x in a])
                def rule(self, *a, **k):
                    print("-" * 60)
            _console = _F()
    return _console


# -- Env helpers

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _port(url: str, default: int) -> int:
    try:
        return int(url.split(":")[2].split("/")[0])
    except Exception:
        return default


def _get_ports() -> tuple[int, int]:
    main_url = _env("JCLAW_MAIN_KOBOLD_URL", "http://127.0.0.1:5002/v1")
    worker_url = _env("JCLAW_WORKER_KOBOLD_URL", "http://127.0.0.1:5003/v1")
    main_port = int(_env("JCLAW_LLAMA_MAIN_PORT") or _port(main_url, 5002))
    worker_port = int(_env("JCLAW_LLAMA_WORKER_PORT") or _port(worker_url, 5003))
    return main_port, worker_port


def _get_host() -> str:
    return _env("JCLAW_LLAMA_HOST", "127.0.0.1")


# -- llama-server auto-discovery

_LLAMA_SEARCH_DIRS = [
    r"P:\llama cpp",
    r"P:\tools",
    r"C:\tools",
    r"C:\llama.cpp",
    r"C:\llama-server",
]


def _find_llama_server() -> Optional[str]:
    configured = _env("JCLAW_LLAMA_SERVER_PATH")
    if configured and Path(configured).exists():
        return configured

    import shutil
    in_path = shutil.which("llama-server") or shutil.which("llama-server.exe")
    if in_path:
        return in_path

    for base in _LLAMA_SEARCH_DIRS:
        p = Path(base)
        if not p.exists():
            continue
        for exe in p.rglob("llama-server.exe"):
            return str(exe)

    return None


# -- Port probe

def _probe_http(url: str, timeout: float = 2.0) -> tuple[bool, int]:
    try:
        import httpx
        r = httpx.get(url, timeout=timeout)
        return r.status_code < 500, r.status_code
    except Exception:
        return False, 0


def _check_port_running(port: int, host: str = "127.0.0.1") -> tuple[bool, int]:
    return _probe_http(f"http://{host}:{port}/v1/models")


# -- PID file

_PIDS_FILE = Path(__file__).parent.parent / ".jclaw-pids.json"


def _load_pids() -> dict:
    if _PIDS_FILE.exists():
        try:
            return json.loads(_PIDS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_pids(pids: dict) -> None:
    _PIDS_FILE.write_text(json.dumps(pids, indent=2))


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], text=True, timeout=3
        )
        return str(pid) in out
    except Exception:
        return False


# -- Stop a port listener

def _kill_port(port: int) -> list[int]:
    killed: list[int] = []
    pids = _load_pids()
    key = str(port)
    if key in pids:
        pid = pids[key]
        if _pid_alive(pid):
            try:
                if os.name == "nt":
                    subprocess.call(["taskkill", "/F", "/PID", str(pid)], timeout=5,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    os.kill(pid, 15)
                killed.append(pid)
            except Exception:
                pass
        del pids[key]
        _save_pids(pids)

    if os.name == "nt":
        try:
            out = subprocess.check_output(["netstat", "-ano"], text=True, timeout=5)
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    if pid not in killed:
                        try:
                            subprocess.call(
                                ["taskkill", "/F", "/PID", str(pid)], timeout=5,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                            )
                            killed.append(pid)
                        except Exception:
                            pass
        except Exception:
            pass

    return killed


# -- Start llama-server

def _start_llama(
    llama_exe: str,
    model_path: str,
    port: int,
    host: str,
    ctx: int,
    gpu_layers: int,
    label: str,
    logs_dir: Path,
) -> Optional[int]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_out = logs_dir / f"llama-{label}.log"
    log_err = logs_dir / f"llama-{label}.err.log"

    args = [
        llama_exe,
        "--model", model_path,
        "--host", host,
        "--port", str(port),
        "--ctx-size", str(ctx),
        "--n-gpu-layers", str(gpu_layers),
    ]

    with open(log_out, "w") as fout, open(log_err, "w") as ferr:
        proc = subprocess.Popen(args, stdout=fout, stderr=ferr, start_new_session=True)

    return proc.pid


def _wait_ready(port: int, host: str, timeout: int = 120, label: str = "llama-server") -> bool:
    url = f"http://{host}:{port}/v1/models"
    for _ in range(timeout):
        ok, _ = _probe_http(url, timeout=2.0)
        if ok:
            return True
        time.sleep(1)
    return False


# -- Agent runner build check

def _check_agent_runner() -> tuple[bool, str]:
    """Return (built, detail)."""
    from .config import AGENT_RUNNER_DIR
    dist_js = AGENT_RUNNER_DIR / "dist" / "index.js"
    if dist_js.exists():
        return True, str(dist_js)
    return False, f"not built — run: jclaw services build"


# -- Credential proxy check

def _check_proxy(port: int) -> tuple[bool, int]:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0):
            return True, 200
    except OSError:
        return False, 0


# -- status

def cmd_services_status() -> int:
    from .config import CREDENTIAL_PROXY_PORT
    main_port, worker_port = _get_ports()
    host = _get_host()

    llama_main_ok, llama_main_code = _check_port_running(main_port, host)
    llama_worker_ok, llama_worker_code = _check_port_running(worker_port, host)
    agent_built, agent_detail = _check_agent_runner()
    proxy_ok, _ = _check_proxy(CREDENTIAL_PROXY_PORT)

    llama_exe = _find_llama_server()
    main_model = _env("JCLAW_MAIN_MODEL_PATH")

    con = _con()

    if _RICH:
        from rich.table import Table
        t = Table(title="J Claw Service Status", show_header=True, header_style="bold cyan")
        t.add_column("Service")
        t.add_column("Status")
        t.add_column("Detail")

        def _row(name: str, ok: bool, detail: str = "") -> None:
            badge = "[green]RUNNING[/green]" if ok else "[red]STOPPED[/red]"
            t.add_row(name, badge, detail)

        _row(f"llama-server/main  (:{main_port})", llama_main_ok,
             f"HTTP {llama_main_code}" if llama_main_ok else "not reachable")
        _row(f"llama-server/worker(:{worker_port})", llama_worker_ok,
             f"HTTP {llama_worker_code}" if llama_worker_ok else "not reachable")
        _row("Agent runner (node)", agent_built, agent_detail)
        _row(f"Credential proxy   (:{CREDENTIAL_PROXY_PORT})", proxy_ok,
             "OK" if proxy_ok else "not started — run: jclaw services start")

        con.print(t)

        hints: list[str] = []
        if not llama_exe:
            hints.append("  [yellow]JCLAW_LLAMA_SERVER_PATH[/yellow] not set")
        if not main_model:
            hints.append("  [yellow]JCLAW_MAIN_MODEL_PATH[/yellow] not set")
        if hints:
            from rich.panel import Panel
            con.print(Panel("\n".join(hints), title="[yellow]Config hints[/yellow]", border_style="yellow"))
    else:
        def _flag(ok: bool) -> str:
            return "OK" if ok else "FAIL"

        print(f"llama-server/main  (:{main_port}): {_flag(llama_main_ok)}")
        print(f"llama-server/worker(:{worker_port}): {_flag(llama_worker_ok)}")
        print(f"Agent runner:      {_flag(agent_built)}  {agent_detail}")
        print(f"Proxy:             {_flag(proxy_ok)}")

    all_ok = llama_main_ok and agent_built and proxy_ok
    return 0 if all_ok else 1


# -- start

def cmd_services_start(main_only: bool = False, no_proxy: bool = False, wait: int = 120) -> int:
    from .config import CREDENTIAL_PROXY_PORT
    main_port, worker_port = _get_ports()
    host = _get_host()
    ctx = int(_env("JCLAW_LLAMA_CONTEXT", "8192"))
    gpu_layers = int(_env("JCLAW_LLAMA_GPU_LAYERS", "999"))
    con = _con()
    logs_dir = Path(__file__).parent.parent / "logs" / "services"
    pids = _load_pids()

    def _print(msg: str) -> None:
        con.print(msg)

    # 1. Agent runner built?
    agent_built, _ = _check_agent_runner()
    _print("1/3 Checking agent runner..." if not _RICH else "[1/3] Checking agent runner…")
    if not agent_built:
        con.print("  Agent runner not built — building now...")
        rc = cmd_services_build()
        if rc != 0:
            return rc
    else:
        con.print("  Agent runner OK" if not _RICH else "  [green]Agent runner OK[/green]")

    # 2. llama-server exe
    _print("2/3 Finding llama-server..." if not _RICH else "[2/3] Finding llama-server…")
    llama_exe = _find_llama_server()
    if not llama_exe:
        con.print("FAIL: llama-server.exe not found.\n  Set JCLAW_LLAMA_SERVER_PATH in .env")
        return 1
    con.print(f"  Found: {llama_exe}")

    main_model = _env("JCLAW_MAIN_MODEL_PATH")
    worker_model = _env("JCLAW_WORKER_MODEL_PATH")

    if not main_model or not Path(main_model).exists():
        con.print("FAIL: JCLAW_MAIN_MODEL_PATH not set or file missing")
        return 1

    _print("3/3 Starting llama-server(s)..." if not _RICH else "[3/3] Starting llama-server(s)…")

    main_ok, _ = _check_port_running(main_port, host)
    if main_ok:
        con.print(f"  llama-server/main already up on :{main_port}")
    else:
        con.print(f"  Starting main :{main_port}...")
        pid = _start_llama(llama_exe, main_model, main_port, host, ctx, gpu_layers, "main", logs_dir)
        if pid:
            pids[str(main_port)] = pid
            _save_pids(pids)
            con.print(f"  PID {pid} waiting...")
            if _wait_ready(main_port, host, wait):
                con.print(f"  llama-server/main ready :{main_port}")
            else:
                con.print(f"  FAIL: not ready in {wait}s")
                return 1

    if not main_only and worker_model and Path(worker_model).exists() and worker_model != main_model:
        worker_ok, _ = _check_port_running(worker_port, host)
        if worker_ok:
            con.print(f"  worker already up :{worker_port}")
        else:
            con.print(f"  Starting worker :{worker_port}...")
            pid = _start_llama(llama_exe, worker_model, worker_port, host, ctx, gpu_layers, "worker", logs_dir)
            if pid:
                pids[str(worker_port)] = pid
                _save_pids(pids)
                con.print(f"  PID {pid} waiting...")
                if _wait_ready(worker_port, host, wait):
                    con.print(f"  worker ready :{worker_port}")
                else:
                    con.print(f"  WARN worker not ready in {wait}s")

    # 4. Credential proxy
    if not no_proxy:
        proxy_ok, _ = _check_proxy(CREDENTIAL_PROXY_PORT)
        if proxy_ok:
            con.print(f"  Proxy already running :{CREDENTIAL_PROXY_PORT}")
        else:
            con.print(f"  Starting credential proxy :{CREDENTIAL_PROXY_PORT}...")
            proxy_log = logs_dir / "proxy.log"
            logs_dir.mkdir(parents=True, exist_ok=True)
            with open(proxy_log, "w") as flog:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "src.main", "start-proxy"],
                    stdout=flog, stderr=flog,
                    start_new_session=True,
                )
            pids["proxy"] = proc.pid
            _save_pids(pids)
            time.sleep(2)
            proxy_ok, _ = _check_proxy(CREDENTIAL_PROXY_PORT)
            if proxy_ok:
                con.print(f"  Proxy ready :{CREDENTIAL_PROXY_PORT}")
            else:
                con.print("  Proxy may still be starting — check logs/services/proxy.log")

    con.print("\nAll services started. Run: jclaw services status")
    return 0


# -- stop

def cmd_services_stop() -> int:
    from .config import CREDENTIAL_PROXY_PORT
    main_port, worker_port = _get_ports()
    con = _con()

    for port, label in [(main_port, "main"), (worker_port, "worker")]:
        killed = _kill_port(port)
        if killed:
            con.print(f"  Stopped llama-server/{label} PIDs {killed}")
        else:
            con.print(f"  :{port} not running")

    pids = _load_pids()
    proxy_pid = pids.pop("proxy", None)
    if proxy_pid and _pid_alive(proxy_pid):
        try:
            if os.name == "nt":
                subprocess.call(["taskkill", "/F", "/PID", str(proxy_pid)], timeout=5,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(proxy_pid, 15)
            con.print(f"  Stopped proxy PID {proxy_pid}")
        except Exception as exc:
            con.print(f"  Could not stop proxy: {exc}")
    _save_pids(pids)
    con.print("Done.")
    return 0


# -- test

def cmd_services_test(prompt: str = "Reply with exactly: JCLAW_OK", no_think: bool = True) -> int:
    import httpx
    from .config import AGENT_RUNNER_DIR, CREDENTIAL_PROXY_PORT
    from .model_registry import load_model_registry

    main_port, worker_port = _get_ports()
    host = _get_host()
    con = _con()
    fail = 0

    def _ok(label: str, detail: str = "") -> None:
        con.print(f"  PASS {label} {detail}")

    def _fail(label: str, detail: str = "") -> None:
        nonlocal fail
        fail += 1
        con.print(f"  FAIL {label} {detail}")

    def _skip(label: str, reason: str = "") -> None:
        con.print(f"  SKIP {label} ({reason})")

    con.print("J Claw smoke test\n")

    # 1. Agent runner
    con.print("1. Agent runner")
    agent_built, agent_detail = _check_agent_runner()
    if agent_built:
        _ok("agent-runner built", agent_detail)
    else:
        _fail("agent-runner", agent_detail)

    # 2. llama-server health
    con.print("\n2. llama-server")
    main_ok, main_code = _check_port_running(main_port, host)
    if main_ok:
        _ok(f"main  :{main_port}", f"HTTP {main_code}")
    else:
        _fail(f"main  :{main_port}", "not reachable — run: jclaw services start")

    worker_ok, worker_code = _check_port_running(worker_port, host)
    if worker_ok:
        _ok(f"worker:{worker_port}", f"HTTP {worker_code}")
    else:
        _skip(f"worker:{worker_port}", "not running (optional)")

    # 3. Model completion request
    con.print("\n3. Model completion")
    if not main_ok:
        _skip("completion", "llama-server/main not running")
    else:
        try:
            registry = load_model_registry()
            ep = registry.resolve("jclaw-main")
            test_prompt = ("/no_think\n" if no_think else "") + prompt
            url = f"{ep.url}/completions" if ep else f"http://{host}:{main_port}/v1/completions"
            models_resp = httpx.get(f"http://{host}:{main_port}/v1/models", timeout=5)
            model_id = models_resp.json()["data"][0]["id"] if models_resp.is_success else "unknown"

            body = {"model": model_id, "prompt": test_prompt, "temperature": 0, "max_tokens": 64}
            headers = {}
            if ep and ep.api_key:
                headers["Authorization"] = f"Bearer {ep.api_key}"

            r = httpx.post(url, json=body, headers=headers, timeout=120)
            if r.is_success:
                text = r.json()["choices"][0]["text"]
                preview = text[:120].replace("\n", " ")
                _ok("completion request", f'model={model_id!r} response="{preview}"')
            else:
                _fail("completion request", f"HTTP {r.status_code}: {r.text[:100]}")
        except Exception as exc:
            _fail("completion request", str(exc)[:120])

    # 4. Credential proxy
    con.print("\n4. Credential proxy")
    proxy_ok, _ = _check_proxy(CREDENTIAL_PROXY_PORT)
    if proxy_ok:
        _ok(f"proxy :{CREDENTIAL_PROXY_PORT}")
    else:
        _fail(f"proxy :{CREDENTIAL_PROXY_PORT}", "not running — run: jclaw services start")

    # 5. Direct agent round-trip
    con.print("\n5. Agent round-trip")
    if not (agent_built and main_ok and proxy_ok):
        _skip("agent round-trip", "prerequisite services not running")
    else:
        import asyncio, json as _json
        from .db import get_registered_groups
        from .container_runner import ContainerInput, run_container_agent

        groups = get_registered_groups()
        if not groups:
            _skip("agent round-trip", "no registered groups")
        else:
            group = list(groups.values())[0]
            inp = ContainerInput(
                prompt="/no_think\nReply with exactly: AGENT_OK",
                session_id=None,
                group_folder=group.folder,
                chat_jid="test@cli",
                is_main=group.is_main,
            )

            result_box: list = []

            def _on_proc(proc, name):
                pass

            async def _run():
                out = await run_container_agent(group, inp, _on_proc)
                result_box.append(out)

            try:
                asyncio.run(_run())
                out = result_box[0] if result_box else None
                if out and out.status == "success":
                    _ok("agent run", f'result="{(out.result or "")[:80]}"')
                else:
                    _fail("agent run", f"status={getattr(out, 'status', '?')} error={getattr(out, 'error', '')[:80]}")
            except Exception as exc:
                _fail("agent run", str(exc)[:120])

    con.print()
    if fail == 0:
        con.print("All tests passed.")
    else:
        con.print(f"{fail} test(s) FAILED.")

    return 0 if fail == 0 else 1


# -- build

def cmd_services_build() -> int:
    """Build the agent-runner node project (npm run build)."""
    from .config import AGENT_RUNNER_DIR
    con = _con()

    pkg = AGENT_RUNNER_DIR / "package.json"
    if not pkg.exists():
        con.print(f"FAIL: {AGENT_RUNNER_DIR}/package.json not found")
        return 1

    con.print(f"Building agent-runner in {AGENT_RUNNER_DIR}...")

    # Install deps if node_modules missing
    node_modules = AGENT_RUNNER_DIR / "node_modules"
    if not node_modules.exists():
        con.print("  Installing npm dependencies...")
        r = subprocess.run(["npm", "install"], cwd=str(AGENT_RUNNER_DIR), timeout=120)
        if r.returncode != 0:
            con.print("  FAIL: npm install failed")
            return 1

    con.print("  Running npm run build...")
    r = subprocess.run(["npm", "run", "build"], cwd=str(AGENT_RUNNER_DIR), timeout=180)
    if r.returncode != 0:
        con.print("  FAIL: npm run build failed")
        return 1

    dist_js = AGENT_RUNNER_DIR / "dist" / "index.js"
    if dist_js.exists():
        con.print(f"  Build complete: {dist_js}")
        return 0
    else:
        con.print("  FAIL: dist/index.js not found after build")
        return 1
