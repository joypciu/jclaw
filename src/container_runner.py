"""Agent runner for J Claw — runs node process directly (no Docker)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .config import (
    AGENT_RUNNER_DIR,
    CONTAINER_MAX_OUTPUT_SIZE,
    CONTAINER_TIMEOUT,
    CREDENTIAL_PROXY_PORT,
    GROUPS_DIR,
    IDLE_TIMEOUT,
    JCLAW_FALLBACK_MODEL,
    JCLAW_MODEL,
    JCLAW_USE_WORKER_MODEL_FOR_SCHEDULED,
    JCLAW_WORKER_MODEL,
    NODE_BIN,
    TIMEZONE,
)
from .container_runtime import PROXY_BIND_HOST
from .credential_proxy import detect_auth_mode
from .group_folder import resolve_group_folder_path, resolve_group_ipc_path
from .logger import logger
from .schema import RegisteredGroup


OUTPUT_START_MARKER = "---JCLAW_OUTPUT_START---"
OUTPUT_END_MARKER = "---JCLAW_OUTPUT_END---"


@dataclass
class ContainerInput:
    prompt: str
    session_id: Optional[str]
    group_folder: str
    chat_jid: str
    is_main: bool
    is_scheduled_task: bool = False
    assistant_name: Optional[str] = None


@dataclass
class ContainerOutput:
    status: str
    result: Optional[str]
    new_session_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AvailableGroup:
    jid: str
    name: str
    last_activity: str
    is_registered: bool


def _prepare_group_env(group: RegisteredGroup, is_main: bool) -> None:
    """Create all necessary directories and files for a group agent run."""
    project_root = Path.cwd()
    group_dir = resolve_group_folder_path(group.folder)
    group_dir.mkdir(parents=True, exist_ok=True)

    # Project-level .claude dir — read by settingSources: ['project'] inside the SDK.
    # Do NOT change HOME/USERPROFILE; real user credentials stay at the real home.
    claude_dir = group_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    settings_file = claude_dir / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "env": {
                    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                    "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD": "1",
                    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0",
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    skills_src = project_root / "container" / "skills"
    skills_dst = claude_dir / "skills"
    if skills_src.exists():
        for skill_dir in skills_src.iterdir():
            if not skill_dir.is_dir():
                continue
            dst_dir = skills_dst / skill_dir.name
            shutil.copytree(skill_dir, dst_dir, dirs_exist_ok=True)

    group_ipc_dir = resolve_group_ipc_path(group.folder)
    (group_ipc_dir / "messages").mkdir(parents=True, exist_ok=True)
    (group_ipc_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (group_ipc_dir / "input").mkdir(parents=True, exist_ok=True)


def _build_agent_env(group: RegisteredGroup, is_main: bool) -> dict[str, str]:
    """Build the environment for the node agent-runner process."""
    project_root = Path.cwd()
    group_dir = resolve_group_folder_path(group.folder)
    group_ipc_dir = resolve_group_ipc_path(group.folder)
    global_dir = GROUPS_DIR / "global"

    env = dict(os.environ)

    # Workspace paths used by the TypeScript agent-runner
    env["JCLAW_WORKSPACE_GROUP"] = str(group_dir)
    env["JCLAW_WORKSPACE_GLOBAL"] = str(global_dir) if global_dir.exists() else str(group_dir)
    env["JCLAW_IPC_INPUT_DIR"] = str(group_ipc_dir / "input")
    env["JCLAW_WORKSPACE_EXTRA"] = str(project_root / "extra")

    # Credential proxy — always localhost since we're on the host.
    # HOME/USERPROFILE are NOT overridden so claude-code finds real credentials.
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{CREDENTIAL_PROXY_PORT}"

    auth = detect_auth_mode()
    if auth == "api-key":
        env["ANTHROPIC_API_KEY"] = "placeholder"
    else:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = "placeholder"

    if JCLAW_MODEL:
        env["JCLAW_MODEL"] = JCLAW_MODEL
    if JCLAW_WORKER_MODEL:
        env["JCLAW_WORKER_MODEL"] = JCLAW_WORKER_MODEL
    if JCLAW_FALLBACK_MODEL:
        env["JCLAW_FALLBACK_MODEL"] = JCLAW_FALLBACK_MODEL
    env["JCLAW_USE_WORKER_MODEL_FOR_SCHEDULED"] = "true" if JCLAW_USE_WORKER_MODEL_FOR_SCHEDULED else "false"
    env["TZ"] = TIMEZONE

    return env


async def run_container_agent(
    group: RegisteredGroup,
    input_data: ContainerInput,
    on_process: Callable[[asyncio.subprocess.Process, str], None],
    on_output: Optional[Callable[[ContainerOutput], Awaitable[None]]] = None,
    *,
    max_retries: int = 2,
) -> ContainerOutput:
    """
    Run the agent-runner node process for a group.

    IPC protocol (Windows-safe):
      - Payload JSON is written to a temp file; path passed as argv[2].
      - stdin is DEVNULL — avoids the Windows asyncio pipe-EOF hang where
        proc.stdin.close() does not reliably signal EOF to the Node process.

    Retry policy (Hermes pattern):
      - Transient errors (timeout, non-zero exit WITHOUT streaming output)
        are retried up to max_retries times with exponential back-off.
      - Errors with partial streaming output are NOT retried (agent produced
        a result; don't double-execute).
    """
    agent_js = AGENT_RUNNER_DIR / "dist" / "index.js"
    if not agent_js.exists():
        return ContainerOutput(
            status="error",
            result=None,
            error=f"Agent runner not built. Run: cd {AGENT_RUNNER_DIR} && npm run build",
        )

    payload = {
        "prompt": input_data.prompt,
        "sessionId": input_data.session_id,
        "groupFolder": input_data.group_folder,
        "chatJid": input_data.chat_jid,
        "isMain": input_data.is_main,
        "isScheduledTask": input_data.is_scheduled_task,
        "assistantName": input_data.assistant_name,
    }

    last_output: ContainerOutput = ContainerOutput(status="error", result=None, error="never started")

    for attempt in range(max_retries + 1):
        if attempt > 0:
            backoff = min(2 ** attempt, 30)
            logger.warning("Agent retry attempt %d/%d for group=%s (backoff=%ds)",
                           attempt, max_retries, group.name, backoff)
            await asyncio.sleep(backoff)

        last_output = await _run_agent_once(
            group, input_data, payload, agent_js, on_process, on_output
        )

        # Don't retry if we got streaming output — partial result is better than double-execute
        if last_output.status == "success":
            return last_output

        # Retry only transient errors (not auth/config failures)
        error_msg = last_output.error or ""
        is_transient = any(kw in error_msg.lower() for kw in ("timeout", "timed out", "exit with code"))
        if not is_transient or attempt >= max_retries:
            break

    return last_output


# Exit codes that Node produces on SIGTERM/SIGINT — treated as clean when we got output
_SIGNAL_EXIT_CODES = frozenset({-15, -2, 130, 143, 1})


async def _run_agent_once(
    group: RegisteredGroup,
    input_data: ContainerInput,
    payload: dict[str, Any],
    agent_js: Path,
    on_process: Callable[[asyncio.subprocess.Process, str], None],
    on_output: Optional[Callable[[ContainerOutput], Awaitable[None]]],
) -> ContainerOutput:
    """Single agent execution attempt (no retry logic)."""
    start = time.time()

    _prepare_group_env(group, input_data.is_main)
    env = _build_agent_env(group, input_data.is_main)

    group_dir = resolve_group_folder_path(group.folder)
    group_dir.mkdir(parents=True, exist_ok=True)

    run_name = f"jclaw-{group.folder}-{int(time.time() * 1000)}"
    logger.info("Spawning agent group=%s run=%s", group.name, run_name)

    logs_dir = group_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Write payload to temp file — Windows-safe IPC (avoids stdin-EOF hang).
    # Node reads argv[2] as a file path and removes the file after parsing.
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="jclaw-input-")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception:
        os.close(tmp_fd)
        raise

    proc = await asyncio.create_subprocess_exec(
        NODE_BIN,
        str(agent_js),
        tmp_path,           # argv[2] — input file path (Node deletes it after reading)
        stdin=asyncio.subprocess.DEVNULL,   # no stdin needed; avoids Windows pipe-EOF bug
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(group_dir),
    )

    on_process(proc, run_name)

    stdout_buf = ""
    stderr_buf = ""
    stdout_truncated = False
    stderr_truncated = False
    parse_buffer = ""
    new_session_id: str | None = None
    had_streaming_output = False
    timed_out = False

    config_timeout = (
        group.container_config.timeout if group.container_config and group.container_config.timeout else CONTAINER_TIMEOUT
    )
    timeout_ms = max(config_timeout, IDLE_TIMEOUT + 30000)

    timeout_task: asyncio.Task[None] | None = None

    async def kill_on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        logger.error("Agent timeout for group=%s run=%s", group.name, run_name)
        try:
            proc.kill()
        except Exception:
            pass

    def reset_timeout() -> None:
        nonlocal timeout_task
        if timeout_task is not None:
            timeout_task.cancel()

        async def _arm() -> None:
            try:
                await asyncio.sleep(timeout_ms / 1000)
                await kill_on_timeout()
            except asyncio.CancelledError:
                return

        timeout_task = asyncio.create_task(_arm())

    reset_timeout()

    close_sentinel = resolve_group_ipc_path(group.folder) / "input" / "_close"
    sentinel_written = False

    async def read_stdout() -> None:
        nonlocal stdout_buf, stdout_truncated, parse_buffer, had_streaming_output, new_session_id, sentinel_written
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(8192)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")

            if not stdout_truncated:
                remaining = CONTAINER_MAX_OUTPUT_SIZE - len(stdout_buf)
                if len(text) > remaining:
                    stdout_buf += text[:remaining]
                    stdout_truncated = True
                else:
                    stdout_buf += text

            parse_buffer += text
            while True:
                start_idx = parse_buffer.find(OUTPUT_START_MARKER)
                if start_idx < 0:
                    break
                end_idx = parse_buffer.find(OUTPUT_END_MARKER, start_idx)
                if end_idx < 0:
                    break

                json_str = parse_buffer[
                    start_idx + len(OUTPUT_START_MARKER) : end_idx
                ].strip()
                parse_buffer = parse_buffer[end_idx + len(OUTPUT_END_MARKER) :]

                # Write the _close sentinel to unblock the agent's waitForIpcMessage()
                if not sentinel_written:
                    try:
                        close_sentinel.touch()
                        sentinel_written = True
                    except Exception:
                        pass

                if on_output is None:
                    continue

                try:
                    parsed = json.loads(json_str)
                    output = ContainerOutput(
                        status=str(parsed.get("status", "error")),
                        result=parsed.get("result") if isinstance(parsed.get("result"), str) else None,
                        new_session_id=(
                            parsed.get("newSessionId")
                            if isinstance(parsed.get("newSessionId"), str)
                            else None
                        ),
                        error=parsed.get("error") if isinstance(parsed.get("error"), str) else None,
                    )
                    if output.new_session_id:
                        new_session_id = output.new_session_id
                    had_streaming_output = True
                    reset_timeout()
                    await on_output(output)
                except Exception:
                    logger.warning("Failed to parse streamed agent output chunk")

    async def read_stderr() -> None:
        nonlocal stderr_buf, stderr_truncated
        assert proc.stderr is not None
        while True:
            chunk = await proc.stderr.read(8192)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            if not stderr_truncated:
                remaining = CONTAINER_MAX_OUTPUT_SIZE - len(stderr_buf)
                if len(text) > remaining:
                    stderr_buf += text[:remaining]
                    stderr_truncated = True
                else:
                    stderr_buf += text

    await asyncio.gather(read_stdout(), read_stderr())
    code = await proc.wait()

    # Clean up temp input file if Node didn't remove it
    try:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    except Exception:
        pass

    if timeout_task is not None:
        timeout_task.cancel()

    duration_ms = int((time.time() - start) * 1000)

    if timed_out:
        if had_streaming_output:
            logger.info("Agent timeout after output (idle cleanup) group=%s", group.name)
            return ContainerOutput(status="success", result=None, new_session_id=new_session_id)
        return ContainerOutput(
            status="error",
            result=None,
            error=f"Agent timed out after {config_timeout}ms",
        )

    ts = datetime_now_stamp()
    log_file = logs_dir / f"agent-{ts}.log"
    log_file.write_text(
        "\n".join(
            [
                "=== Agent Run Log ===",
                f"Group: {group.name}",
                f"Run: {run_name}",
                f"Duration: {duration_ms}ms",
                f"Exit Code: {code}",
                f"Stdout Truncated: {stdout_truncated}",
                f"Stderr Truncated: {stderr_truncated}",
                "",
                "=== Stderr ===",
                stderr_buf,
                "",
                "=== Stdout ===",
                stdout_buf,
            ]
        ),
        encoding="utf-8",
    )

    # SIGTERM/SIGINT after streaming output = clean shutdown, not an error.
    # Node emits "got 3 SIGTERM/SIGINTs, forcefully exiting" on these codes.
    if code != 0:
        if had_streaming_output and code in _SIGNAL_EXIT_CODES:
            logger.debug("Agent exited with signal code %d after output — treating as success (group=%s)", code, group.name)
        else:
            return ContainerOutput(
                status="error",
                result=None,
                error=f"Agent exited with code {code}: {stderr_buf[-200:]}",
            )

    if on_output is not None:
        return ContainerOutput(status="success", result=None, new_session_id=new_session_id)

    try:
        start_idx = stdout_buf.find(OUTPUT_START_MARKER)
        end_idx = stdout_buf.find(OUTPUT_END_MARKER)
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_line = stdout_buf[start_idx + len(OUTPUT_START_MARKER) : end_idx].strip()
        else:
            lines = [ln for ln in stdout_buf.splitlines() if ln.strip()]
            json_line = lines[-1] if lines else "{}"

        parsed = json.loads(json_line)
        return ContainerOutput(
            status=str(parsed.get("status", "error")),
            result=parsed.get("result") if isinstance(parsed.get("result"), str) else None,
            new_session_id=(
                parsed.get("newSessionId")
                if isinstance(parsed.get("newSessionId"), str)
                else None
            ),
            error=parsed.get("error") if isinstance(parsed.get("error"), str) else None,
        )
    except Exception as exc:
        return ContainerOutput(
            status="error",
            result=None,
            error=f"Failed to parse agent output: {exc}",
        )


def datetime_now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())


def write_tasks_snapshot(
    group_folder: str,
    is_main: bool,
    tasks: list[dict[str, object]],
) -> None:
    group_ipc_dir = resolve_group_ipc_path(group_folder)
    group_ipc_dir.mkdir(parents=True, exist_ok=True)

    filtered = tasks if is_main else [t for t in tasks if t.get("groupFolder") == group_folder]
    tasks_file = group_ipc_dir / "current_tasks.json"
    tasks_file.write_text(json.dumps(filtered, indent=2), encoding="utf-8")


def write_groups_snapshot(
    group_folder: str,
    is_main: bool,
    groups: list[AvailableGroup],
    _registered_jids: set[str],
) -> None:
    group_ipc_dir = resolve_group_ipc_path(group_folder)
    group_ipc_dir.mkdir(parents=True, exist_ok=True)

    visible = groups if is_main else []
    payload = {
        "groups": [
            {
                "jid": g.jid,
                "name": g.name,
                "lastActivity": g.last_activity,
                "isRegistered": g.is_registered,
            }
            for g in visible
        ],
        "lastSync": datetime_now_iso(),
    }

    groups_file = group_ipc_dir / "available_groups.json"
    groups_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def datetime_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
