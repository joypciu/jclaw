"""Remote control process management for Claude CLI."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .config import DATA_DIR
from .logger import logger


@dataclass
class RemoteControlSession:
    pid: int
    url: str
    started_by: str
    started_in_chat: str
    started_at: str


_active_session: RemoteControlSession | None = None

_URL_REGEX = re.compile(r"https://claude\.ai/code\S+")
_URL_TIMEOUT_S = 30.0
_URL_POLL_S = 0.2
_STATE_FILE = Path(DATA_DIR) / "remote-control.json"
_STDOUT_FILE = Path(DATA_DIR) / "remote-control.stdout"
_STDERR_FILE = Path(DATA_DIR) / "remote-control.stderr"


def _save_state(session: RemoteControlSession) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(asdict(session)), encoding="utf-8")


def _clear_state() -> None:
    try:
        _STATE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def restore_remote_control() -> None:
    global _active_session

    try:
        raw = _STATE_FILE.read_text(encoding="utf-8")
    except Exception:
        return

    try:
        data = json.loads(raw)
        session = RemoteControlSession(
            pid=int(data["pid"]),
            url=str(data["url"]),
            started_by=str(data["started_by"]),
            started_in_chat=str(data["started_in_chat"]),
            started_at=str(data["started_at"]),
        )
    except Exception:
        _clear_state()
        return

    if _is_process_alive(session.pid):
        _active_session = session
        logger.info("Restored Remote Control session pid=%s url=%s", session.pid, session.url)
    else:
        _clear_state()


def get_active_session() -> RemoteControlSession | None:
    return _active_session


def reset_remote_control_for_tests() -> None:
    global _active_session
    _active_session = None


def get_state_file_path() -> str:
    return str(_STATE_FILE)


def start_remote_control(
    sender: str,
    chat_jid: str,
    cwd: str,
) -> dict[str, object]:
    global _active_session

    if _active_session and _is_process_alive(_active_session.pid):
        return {"ok": True, "url": _active_session.url}

    _active_session = None
    _clear_state()

    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

    try:
        out_fd = open(_STDOUT_FILE, "w", encoding="utf-8")
        err_fd = open(_STDERR_FILE, "w", encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"Failed to open log files: {exc}"}

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]

    try:
        proc = subprocess.Popen(
            ["claude", "remote-control", "--name", "NanoClaw Remote"],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=out_fd,
            stderr=err_fd,
            text=True,
            start_new_session=(os.name != "nt"),
            creationflags=creationflags,
        )
    except Exception as exc:
        out_fd.close()
        err_fd.close()
        return {"ok": False, "error": f"Failed to start: {exc}"}

    try:
        if proc.stdin:
            proc.stdin.write("y\n")
            proc.stdin.flush()
            proc.stdin.close()
    except Exception:
        pass

    out_fd.close()
    err_fd.close()

    pid = proc.pid
    start_time = time.time()

    while True:
        if not _is_process_alive(pid):
            return {"ok": False, "error": "Process exited before producing URL"}

        try:
            content = _STDOUT_FILE.read_text(encoding="utf-8")
        except Exception:
            content = ""

        m = _URL_REGEX.search(content)
        if m:
            session = RemoteControlSession(
                pid=pid,
                url=m.group(0),
                started_by=sender,
                started_in_chat=chat_jid,
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            _active_session = session
            _save_state(session)
            logger.info(
                "Remote Control session started pid=%s url=%s sender=%s chat=%s",
                pid,
                session.url,
                sender,
                chat_jid,
            )
            return {"ok": True, "url": session.url}

        if (time.time() - start_time) >= _URL_TIMEOUT_S:
            try:
                if os.name == "nt":
                    os.kill(pid, signal.SIGTERM)
                else:
                    os.killpg(pid, signal.SIGTERM)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
            return {"ok": False, "error": "Timed out waiting for Remote Control URL"}

        time.sleep(_URL_POLL_S)


def stop_remote_control() -> dict[str, object]:
    global _active_session

    if _active_session is None:
        return {"ok": False, "error": "No active Remote Control session"}

    pid = _active_session.pid
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass

    _active_session = None
    _clear_state()
    logger.info("Remote Control session stopped pid=%s", pid)
    return {"ok": True}
