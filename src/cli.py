"""Rich-formatted interactive CLI for J Claw.

Provides two modes of operation:

  jclaw chat                # interactive REPL — type prompts, see streamed responses
  jclaw prompt "do thing"   # single-shot prompt, exits when done

The CLI routes prompts through the credential proxy to a container agent,
just like a chat channel would.  It uses the jclaw database to discover
registered groups and the .env file for configuration — no separate setup.

Commands available inside the interactive REPL
────────────────────────────────────────────────
  /help              show command list
  /groups            list registered groups
  /group <name>      switch active group
  /provider list     show model aliases
  /provider test     probe backend health
  /session           show current session ID
  /clear             clear the screen
  /quit  or  /exit   exit the REPL

Usage examples
──────────────
  # Interactive session with the main group
  jclaw chat

  # Single prompt (scripting-friendly)
  jclaw prompt "summarise the last 10 messages"

  # Target a specific group
  jclaw chat --group research

  # Pipe input
  echo "what's the weather?" | jclaw prompt --pipe
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# ── Rich imports (soft-dep: degrade gracefully if somehow absent) ─────────────
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
    _RICH = True
except ImportError:
    _RICH = False

_console: Any = None


def _get_console() -> Any:
    global _console
    if _console is None:
        if _RICH:
            from rich.console import Console
            _console = Console()
        else:
            class _FallbackConsole:
                def print(self, *args: object, **kw: object) -> None:
                    print(*args)
                def rule(self, *a: object, **k: object) -> None:
                    print("-" * 60)
            _console = _FallbackConsole()
    return _console


# ── DB helpers (local import to avoid circular deps) ─────────────────────────

def _load_env(root: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = root / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip())
    return env


def _ensure_env(root: Path) -> None:
    for k, v in _load_env(root).items():
        if k not in os.environ:
            os.environ[k] = v


# ── Group discovery ───────────────────────────────────────────────────────────

def _list_groups() -> list[dict]:
    try:
        from .db import get_all_registered_groups
        groups = get_all_registered_groups()
        return [{"name": g.name, "folder": g.folder, "is_main": bool(g.is_main)} for g in groups.values()]
    except Exception:
        return []


def _resolve_group(name: str) -> dict | None:
    for g in _list_groups():
        if name.lower() in (g["name"].lower(), g["folder"].lower()):
            return g
    # fallback: main group
    for g in _list_groups():
        if g["is_main"]:
            return g
    all_groups = _list_groups()
    return all_groups[0] if all_groups else None


# ── Prompt dispatch ───────────────────────────────────────────────────────────

async def _run_prompt(
    prompt_text: str,
    group: dict,
    session_id: str | None,
) -> str | None:
    """Send a prompt to a container agent and collect the output."""
    from .container_runner import ContainerInput, ContainerOutput, run_container_agent, write_groups_snapshot, write_tasks_snapshot
    from .db import get_all_tasks, set_session
    from .group_folder import resolve_group_folder_path

    console = _get_console()
    collected: list[str] = []

    async def on_output(output: ContainerOutput) -> None:
        if output.result:
            if _RICH:
                console.print(Markdown(output.result))
            else:
                console.print(output.result)
            collected.append(output.result)
        if output.new_session_id:
            _run_prompt._last_session = output.new_session_id

    # Minimal group registration needed to run
    from .schema import RegisteredGroup
    _cli_jid = group.get("jid", f"cli-{group['folder']}@cli")
    rg = RegisteredGroup(
        name=group["name"],
        folder=group["folder"],
        trigger="",
        added_at="",
        is_main=group.get("is_main", False),
    )

    try:
        group_dir = resolve_group_folder_path(rg.folder)
    except Exception:
        group_dir = Path.cwd() / "groups" / rg.folder
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / "logs").mkdir(parents=True, exist_ok=True)

    tasks = get_all_tasks()
    write_tasks_snapshot(rg.folder, rg.is_main or False, [
        {"id": t.id, "groupFolder": t.group_folder, "prompt": t.prompt,
         "schedule_type": t.schedule_type, "schedule_value": t.schedule_value,
         "status": t.status, "next_run": t.next_run}
        for t in tasks
    ])
    write_groups_snapshot(rg.folder, rg.is_main or False, [], set())

    inp = ContainerInput(
        prompt=prompt_text,
        session_id=session_id,
        group_folder=rg.folder,
        chat_jid=_cli_jid,
        is_main=rg.is_main or False,
        is_scheduled_task=False,
        assistant_name=os.environ.get("ASSISTANT_NAME", "JClaw"),
    )

    from .group_queue import GroupQueue
    _queue = GroupQueue()

    output = await run_container_agent(
        rg,
        inp,
        lambda proc, cname: _queue.register_process(_cli_jid, proc, cname, rg.folder),
        on_output,
    )

    if output.new_session_id:
        set_session(rg.folder, output.new_session_id)

    return getattr(_run_prompt, "_last_session", None)


_run_prompt._last_session = None  # type: ignore[attr-defined]


# ── REPL helpers ──────────────────────────────────────────────────────────────

def _print_greeting(group_name: str) -> None:
    console = _get_console()
    if _RICH:
        console.print(Panel(
            f"[bold cyan]J Claw[/bold cyan] interactive chat\n"
            f"Group: [yellow]{group_name}[/yellow]  •  type [dim]/help[/dim] for commands",
            border_style="cyan",
            expand=False,
        ))
    else:
        console.print(f"J Claw chat — group: {group_name} (type /help)")


def _print_help() -> None:
    console = _get_console()
    if not _RICH:
        console.print("/groups  /group <name>  /provider list  /provider test  /session  /clear  /quit")
        return
    t = Table(title="Available commands", show_header=True, header_style="bold cyan")
    t.add_column("Command", style="green")
    t.add_column("Description")
    for cmd, desc in [
        ("/help", "Show this list"),
        ("/groups", "List registered groups"),
        ("/group <name>", "Switch active group"),
        ("/provider list", "Show model aliases"),
        ("/provider test", "Probe backend health"),
        ("/session", "Show current session ID"),
        ("/clear", "Clear the screen"),
        ("/quit / /exit", "Exit the REPL"),
    ]:
        t.add_row(cmd, desc)
    console.print(t)


def _print_groups(current_folder: str) -> None:
    console = _get_console()
    groups = _list_groups()
    if not groups:
        console.print("[yellow]No registered groups found.[/yellow]")
        return
    if _RICH:
        t = Table(title="Registered groups", show_header=True, header_style="bold")
        t.add_column("Name")
        t.add_column("Folder")
        t.add_column("Main")
        for g in groups:
            marker = "[green]✓[/green]" if g["folder"] == current_folder else ""
            t.add_row(g["name"], g["folder"], "yes" if g["is_main"] else "", end_section=False)
        console.print(t)
    else:
        for g in groups:
            active = " *" if g["folder"] == current_folder else ""
            console.print(f"  {g['name']} ({g['folder']}){active}")


def _provider_list() -> None:
    console = _get_console()
    try:
        from .model_registry import load_model_registry
        reg = load_model_registry()
        aliases = reg.all_aliases()
        if not aliases:
            console.print("[yellow]No model aliases configured.[/yellow]")
            return
        if _RICH:
            t = Table(title="Model aliases", show_header=True, header_style="bold")
            t.add_column("Alias", style="cyan")
            t.add_column("URL")
            t.add_column("Model")
            for a in sorted(aliases):
                ep = reg.resolve(a)
                if ep:
                    t.add_row(a, ep.url, ep.model)
            console.print(t)
        else:
            for a in sorted(aliases):
                ep = reg.resolve(a)
                if ep:
                    console.print(f"  {a}: {ep.url}  ({ep.model})")
    except Exception as exc:
        console.print(f"[red]Error loading registry: {exc}[/red]")


def _provider_test() -> None:
    import httpx
    console = _get_console()
    try:
        from .model_registry import load_model_registry
        reg = load_model_registry()
        for a in sorted(reg.all_aliases()):
            ep = reg.resolve(a)
            if not ep:
                continue
            try:
                r = httpx.get(f"{ep.url}/models", timeout=3.0,
                              headers={"Authorization": f"Bearer {ep.api_key}"} if ep.api_key else {})
                status = r.status_code
                if _RICH:
                    color = "green" if status < 400 else "red"
                    console.print(f"[{color}]  {a}: HTTP {status}[/{color}]")
                else:
                    console.print(f"  {a}: HTTP {status}")
            except Exception as exc:
                if _RICH:
                    console.print(f"[red]  {a}: {exc.__class__.__name__}[/red]")
                else:
                    console.print(f"  {a}: FAIL ({exc})")
    except Exception as exc:
        console.print(f"Error: {exc}")


# ── Public entry points ───────────────────────────────────────────────────────

def run_chat(group_name: str = "", pipe_mode: bool = False, session_id: str | None = None) -> int:
    """Interactive REPL or --pipe single-shot mode."""
    root = Path.cwd()
    _ensure_env(root)

    from .db import init_database
    init_database()

    console = _get_console()

    group = _resolve_group(group_name) if group_name else _resolve_group("")
    if group is None:
        console.print("[red]No registered groups found. Register a group first.[/red]")
        return 1

    current_session: str | None = session_id

    # ── pipe mode ─────────────────────────────────────────────────────────
    if pipe_mode:
        prompt_text = sys.stdin.read().strip()
        if not prompt_text:
            console.print("[red]No input provided via stdin.[/red]")
            return 1
        current_session = asyncio.run(_run_prompt(prompt_text, group, current_session))
        return 0

    # ── interactive REPL ──────────────────────────────────────────────────
    _print_greeting(group["name"])

    while True:
        try:
            if _RICH:
                user_input = Prompt.ask(f"[cyan]>{group['name']}[/cyan]").strip()
            else:
                user_input = input(f">{group['name']}> ").strip()
        except (EOFError, KeyboardInterrupt):
            if _RICH:
                console.print("\n[dim]bye[/dim]")
            else:
                console.print("\nbye")
            break

        if not user_input:
            continue

        # ── built-in commands ──────────────────────────────────────────────
        if user_input.startswith("/"):
            parts = user_input.split()
            cmd = parts[0].lower()

            if cmd in ("/quit", "/exit", "/bye"):
                if _RICH:
                    console.print("[dim]bye[/dim]")
                break

            elif cmd == "/help":
                _print_help()

            elif cmd == "/clear":
                if _RICH:
                    console.clear()
                else:
                    os.system("cls" if os.name == "nt" else "clear")

            elif cmd == "/groups":
                _print_groups(group["folder"])

            elif cmd == "/group":
                target = " ".join(parts[1:]) if len(parts) > 1 else ""
                new_group = _resolve_group(target)
                if new_group:
                    group = new_group
                    current_session = None
                    console.print(f"Switched to group: [yellow]{group['name']}[/yellow]" if _RICH else f"Switched to {group['name']}")
                else:
                    console.print(f"[red]Group '{target}' not found.[/red]" if _RICH else f"Group not found: {target}")

            elif cmd == "/provider":
                sub = parts[1].lower() if len(parts) > 1 else "list"
                if sub == "list":
                    _provider_list()
                elif sub == "test":
                    _provider_test()
                else:
                    console.print(f"Unknown: /provider {sub}" )

            elif cmd == "/session":
                if current_session:
                    console.print(f"Session: [dim]{current_session}[/dim]" if _RICH else f"Session: {current_session}")
                else:
                    console.print("[dim]No active session[/dim]" if _RICH else "No active session")

            else:
                console.print(f"[red]Unknown command: {cmd}[/red]" if _RICH else f"Unknown command: {cmd}")
            continue

        # ── forward to agent ────────────────────────────────────────────────
        if _RICH:
            console.rule(style="dim")
        try:
            new_session = asyncio.run(_run_prompt(user_input, group, current_session))
            if new_session:
                current_session = new_session
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]" if _RICH else f"Error: {exc}")

        if _RICH:
            console.rule(style="dim")

    return 0


def run_single_prompt(
    prompt_text: str,
    group_name: str = "",
    session_id: str | None = None,
) -> int:
    """Send a single prompt and print the result."""
    root = Path.cwd()
    _ensure_env(root)
    from .db import init_database
    init_database()

    console = _get_console()
    group = _resolve_group(group_name) if group_name else _resolve_group("")
    if group is None:
        console.print("[red]No registered groups found.[/red]" if _RICH else "No registered groups found.")
        return 1

    asyncio.run(_run_prompt(prompt_text, group, session_id))
    return 0
