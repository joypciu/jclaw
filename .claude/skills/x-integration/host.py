"""X integration IPC handler (Python host-side runtime)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _run_script(script: str, args: dict[str, Any]) -> dict[str, Any]:
    script_path = Path.cwd() / ".claude" / "skills" / "x-integration" / "scripts" / f"{script}.py"
    if not script_path.exists():
        return {"success": False, "message": f"Script not found: {script_path}"}

    env = dict(__import__("os").environ)
    env["NANOCLAW_ROOT"] = str(Path.cwd())

    try:
        proc = subprocess.run(
            ["python", str(script_path)],
            input=json.dumps(args),
            text=True,
            capture_output=True,
            timeout=120,
            env=env,
            cwd=str(Path.cwd()),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Script timed out (120s)"}
    except Exception as exc:
        return {"success": False, "message": f"Failed to spawn script: {exc}"}

    if proc.returncode != 0:
        msg = proc.stderr.strip() or f"Script exited with code: {proc.returncode}"
        return {"success": False, "message": msg}

    out = proc.stdout.strip().splitlines()
    if not out:
        return {"success": False, "message": "Script produced no output"}

    try:
        return json.loads(out[-1])
    except Exception:
        return {"success": False, "message": f"Failed to parse output: {out[-1][:200]}"}


def _write_result(data_dir: str, source_group: str, request_id: str, result: dict[str, Any]) -> None:
    results_dir = Path(data_dir) / "ipc" / source_group / "x_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{request_id}.json").write_text(json.dumps(result), encoding="utf-8")


async def handle_x_ipc(data: dict[str, Any], source_group: str, is_main: bool, data_dir: str) -> bool:
    ipc_type = str(data.get("type") or "")
    if not ipc_type.startswith("x_"):
        return False

    request_id = str(data.get("requestId") or "")
    if not request_id:
        return True

    if not is_main:
        _write_result(
            data_dir,
            source_group,
            request_id,
            {"success": False, "message": "Only the main group can interact with X."},
        )
        return True

    if ipc_type == "x_post":
        result = _run_script("post", {"content": data.get("content")})
    elif ipc_type == "x_like":
        result = _run_script("like", {"tweetUrl": data.get("tweetUrl")})
    elif ipc_type == "x_reply":
        result = _run_script("reply", {"tweetUrl": data.get("tweetUrl"), "content": data.get("content")})
    elif ipc_type == "x_retweet":
        result = _run_script("retweet", {"tweetUrl": data.get("tweetUrl")})
    elif ipc_type == "x_quote":
        result = _run_script("quote", {"tweetUrl": data.get("tweetUrl"), "comment": data.get("comment")})
    else:
        return False

    _write_result(data_dir, source_group, request_id, result)
    return True
