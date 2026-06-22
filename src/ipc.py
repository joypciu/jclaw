"""IPC watcher and command processing for group-scoped control files."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol
from zoneinfo import ZoneInfo

from .config import DATA_DIR, IPC_POLL_INTERVAL, TIMEZONE
from .db import create_task, delete_task, get_task_by_id, update_task
from .group_folder import is_valid_group_folder
from .logger import logger
from .schema import RegisteredGroup, ScheduledTask


@dataclass
class AvailableGroup:
    jid: str
    name: str
    last_activity: str
    is_registered: bool


class IpcDeps(Protocol):
    async def send_message(self, jid: str, text: str) -> None: ...
    def registered_groups(self) -> dict[str, RegisteredGroup]: ...
    def register_group(self, jid: str, group: RegisteredGroup) -> None: ...
    async def sync_groups(self, force: bool) -> None: ...
    def get_available_groups(self) -> list[AvailableGroup]: ...
    def write_groups_snapshot(
        self,
        group_folder: str,
        is_main: bool,
        available_groups: list[AvailableGroup],
        registered_jids: set[str],
    ) -> None: ...
    def on_tasks_changed(self) -> None: ...


_watcher_task: asyncio.Task[None] | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_task_id() -> str:
    return f"task-{int(time.time() * 1000)}-{random.randint(100000, 999999)}"


def _get_croniter() -> Callable[..., Any] | None:
    try:
        mod = importlib.import_module("croniter")
        return getattr(mod, "croniter", None)
    except Exception:
        return None


def _compute_initial_next_run(schedule_type: str, schedule_value: str) -> str | None:
    now = _utc_now()

    if schedule_type == "cron":
        croniter_cls = _get_croniter()
        if croniter_cls is None:
            logger.warning("croniter not installed; falling back to +1 minute")
            return (now + timedelta(minutes=1)).isoformat()

        try:
            base = now.astimezone(ZoneInfo(TIMEZONE))
            itr = croniter_cls(schedule_value, base)
            nxt = itr.get_next(datetime)
            return nxt.astimezone(timezone.utc).isoformat()
        except Exception:
            return None

    if schedule_type == "interval":
        try:
            ms = int(schedule_value)
        except ValueError:
            return None
        if ms <= 0:
            return None
        return (now + timedelta(milliseconds=ms)).isoformat()

    if schedule_type == "once":
        try:
            parsed = datetime.fromisoformat(schedule_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.isoformat()
        except ValueError:
            return None

    return None


async def _handle_host_browser_ipc(data: dict[str, Any], source_group: str) -> bool:
    try:
        mod = importlib.import_module(".host_browser", package=__package__)
        handler = getattr(mod, "handle_host_browser_ipc", None)
        if callable(handler):
            result = handler(data, source_group, str(DATA_DIR))
            if asyncio.iscoroutine(result):
                return bool(await result)
            return bool(result)
    except Exception:
        return False

    return False


def _load_x_integration_handler() -> Optional[Callable[[dict[str, Any], str, bool, str], Any]]:
    skill_path = Path.cwd() / ".claude" / "skills" / "x-integration" / "host.py"
    if not skill_path.exists():
        return None

    try:
        spec = importlib.util.spec_from_file_location("jclaw_x_integration_host", str(skill_path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        handler = getattr(mod, "handle_x_ipc", None)
        return handler if callable(handler) else None
    except Exception:
        return None


async def _handle_x_integration_ipc(
    data: dict[str, Any],
    source_group: str,
    is_main: bool,
) -> bool:
    handler = _load_x_integration_handler()
    if handler is None:
        return False

    try:
        result = handler(data, source_group, is_main, str(DATA_DIR))
        if asyncio.iscoroutine(result):
            return bool(await result)
        return bool(result)
    except Exception:
        return False


async def process_task_ipc(
    data: dict[str, Any],
    source_group: str,
    is_main: bool,
    deps: IpcDeps,
) -> None:
    registered_groups = deps.registered_groups()

    if await _handle_host_browser_ipc(data, source_group):
        return

    if await _handle_x_integration_ipc(data, source_group, is_main):
        return

    ipc_type = data.get("type")

    if ipc_type == "schedule_task":
        prompt_raw = data.get("prompt")
        schedule_type_raw = data.get("schedule_type")
        schedule_value_raw = data.get("schedule_value")
        target_jid_raw = data.get("targetJid")

        if not isinstance(prompt_raw, str) or not prompt_raw:
            return
        if not isinstance(schedule_type_raw, str) or not schedule_type_raw:
            return
        if not isinstance(schedule_value_raw, str) or not schedule_value_raw:
            return
        if not isinstance(target_jid_raw, str) or not target_jid_raw:
            return

        prompt = prompt_raw
        schedule_type = schedule_type_raw
        schedule_value = schedule_value_raw
        target_jid = target_jid_raw

        target_group = registered_groups.get(target_jid)
        if target_group is None:
            logger.warning("Cannot schedule task: target group not registered (%s)", target_jid)
            return

        target_folder = target_group.folder
        if (not is_main) and (target_folder != source_group):
            logger.warning("Unauthorized schedule_task blocked source=%s target=%s", source_group, target_folder)
            return

        if schedule_type not in {"cron", "interval", "once"}:
            logger.warning("Invalid schedule_type: %s", schedule_type)
            return

        next_run = _compute_initial_next_run(schedule_type, schedule_value)
        if next_run is None:
            logger.warning("Invalid schedule value for type=%s value=%s", schedule_type, schedule_value)
            return

        context_mode = data.get("context_mode")
        if context_mode not in {"group", "isolated"}:
            context_mode = "isolated"

        task_id_raw = data.get("taskId")
        task_id = task_id_raw if isinstance(task_id_raw, str) and task_id_raw else _build_task_id()

        create_task(
            ScheduledTask(
                id=task_id,
                group_folder=target_folder,
                chat_jid=target_jid,
                prompt=prompt,
                schedule_type=schedule_type,
                schedule_value=schedule_value,
                context_mode=context_mode,
                next_run=next_run,
                last_run=None,
                last_result=None,
                status="active",
                created_at=_utc_now().isoformat(),
            )
        )
        logger.info("Task created via IPC id=%s source=%s target=%s", task_id, source_group, target_folder)
        deps.on_tasks_changed()
        return

    if ipc_type == "pause_task":
        task_id = data.get("taskId")
        if isinstance(task_id, str):
            task = get_task_by_id(task_id)
            if task and (is_main or task.group_folder == source_group):
                update_task(task_id, status="paused")
                logger.info("Task paused via IPC id=%s source=%s", task_id, source_group)
                deps.on_tasks_changed()
            else:
                logger.warning("Unauthorized task pause attempt id=%s source=%s", task_id, source_group)
        return

    if ipc_type == "resume_task":
        task_id = data.get("taskId")
        if isinstance(task_id, str):
            task = get_task_by_id(task_id)
            if task and (is_main or task.group_folder == source_group):
                update_task(task_id, status="active")
                logger.info("Task resumed via IPC id=%s source=%s", task_id, source_group)
                deps.on_tasks_changed()
            else:
                logger.warning("Unauthorized task resume attempt id=%s source=%s", task_id, source_group)
        return

    if ipc_type == "cancel_task":
        task_id = data.get("taskId")
        if isinstance(task_id, str):
            task = get_task_by_id(task_id)
            if task and (is_main or task.group_folder == source_group):
                delete_task(task_id)
                logger.info("Task cancelled via IPC id=%s source=%s", task_id, source_group)
                deps.on_tasks_changed()
            else:
                logger.warning("Unauthorized task cancel attempt id=%s source=%s", task_id, source_group)
        return

    if ipc_type == "update_task":
        task_id = data.get("taskId")
        if not isinstance(task_id, str):
            return

        task = get_task_by_id(task_id)
        if task is None:
            logger.warning("Task not found for update id=%s source=%s", task_id, source_group)
            return

        if (not is_main) and (task.group_folder != source_group):
            logger.warning("Unauthorized task update attempt id=%s source=%s", task_id, source_group)
            return

        updates: dict[str, Any] = {}
        if "prompt" in data and isinstance(data.get("prompt"), str):
            updates["prompt"] = data["prompt"]
        if "schedule_type" in data and data.get("schedule_type") in {"cron", "interval", "once"}:
            updates["schedule_type"] = data["schedule_type"]
        if "schedule_value" in data and isinstance(data.get("schedule_value"), str):
            updates["schedule_value"] = data["schedule_value"]

        if "schedule_type" in updates or "schedule_value" in updates:
            schedule_type = updates.get("schedule_type", task.schedule_type)
            schedule_value = updates.get("schedule_value", task.schedule_value)
            next_run = _compute_initial_next_run(schedule_type, schedule_value)
            if next_run is None:
                logger.warning("Invalid schedule update for task id=%s", task_id)
                return
            updates["next_run"] = next_run

        if updates:
            update_task(task_id, **updates)
            logger.info("Task updated via IPC id=%s source=%s updates=%s", task_id, source_group, updates)
            deps.on_tasks_changed()
        return

    if ipc_type == "refresh_groups":
        if is_main:
            logger.info("Group refresh requested via IPC source=%s", source_group)
            await deps.sync_groups(True)
            available = deps.get_available_groups()
            deps.write_groups_snapshot(
                source_group,
                True,
                available,
                set(registered_groups.keys()),
            )
        else:
            logger.warning("Unauthorized refresh_groups blocked source=%s", source_group)
        return

    if ipc_type == "register_group":
        if not is_main:
            logger.warning("Unauthorized register_group blocked source=%s", source_group)
            return

        jid_raw = data.get("jid")
        name_raw = data.get("name")
        folder_raw = data.get("folder")
        trigger_raw = data.get("trigger")

        if not isinstance(jid_raw, str) or not jid_raw:
            logger.warning("Invalid register_group request missing fields")
            return
        if not isinstance(name_raw, str) or not name_raw:
            logger.warning("Invalid register_group request missing fields")
            return
        if not isinstance(folder_raw, str) or not folder_raw:
            logger.warning("Invalid register_group request missing fields")
            return
        if not isinstance(trigger_raw, str) or not trigger_raw:
            logger.warning("Invalid register_group request missing fields")
            return

        jid = jid_raw
        name = name_raw
        folder = folder_raw
        trigger = trigger_raw

        if not is_valid_group_folder(folder):
            logger.warning("Invalid register_group folder=%s source=%s", folder, source_group)
            return

        requires_trigger = data.get("requiresTrigger")
        if not isinstance(requires_trigger, bool):
            requires_trigger = None

        deps.register_group(
            jid,
            RegisteredGroup(
                name=name,
                folder=folder,
                trigger=trigger,
                added_at=_utc_now().isoformat(),
                container_config=None,
                requires_trigger=requires_trigger,
                is_main=None,
            ),
        )
        return

    logger.warning("Unknown IPC task type: %s", ipc_type)


def start_ipc_watcher(deps: IpcDeps) -> None:
    global _watcher_task

    if _watcher_task and not _watcher_task.done():
        logger.debug("IPC watcher already running, skipping duplicate start")
        return

    ipc_base_dir = Path(DATA_DIR) / "ipc"
    ipc_base_dir.mkdir(parents=True, exist_ok=True)

    async def _loop() -> None:
        while True:
            try:
                group_folders = [
                    p.name
                    for p in ipc_base_dir.iterdir()
                    if p.is_dir() and p.name != "errors"
                ]
            except Exception as exc:
                logger.error("Error reading IPC base directory: %s", exc)
                await asyncio.sleep(IPC_POLL_INTERVAL)
                continue

            registered = deps.registered_groups()
            folder_is_main: dict[str, bool] = {
                g.folder: bool(g.is_main) for g in registered.values()
            }

            for source_group in group_folders:
                is_main = folder_is_main.get(source_group, False)
                messages_dir = ipc_base_dir / source_group / "messages"
                tasks_dir = ipc_base_dir / source_group / "tasks"

                if messages_dir.exists():
                    for file_path in [p for p in messages_dir.iterdir() if p.suffix == ".json"]:
                        try:
                            data = json.loads(file_path.read_text(encoding="utf-8"))
                            if (
                                data.get("type") == "message"
                                and isinstance(data.get("chatJid"), str)
                                and isinstance(data.get("text"), str)
                            ):
                                target_group = registered.get(data["chatJid"])
                                if is_main or (target_group and target_group.folder == source_group):
                                    await deps.send_message(data["chatJid"], data["text"])
                                    logger.info("IPC message sent chat=%s source=%s", data["chatJid"], source_group)
                                else:
                                    logger.warning("Unauthorized IPC message blocked chat=%s source=%s", data["chatJid"], source_group)
                            file_path.unlink(missing_ok=True)
                        except Exception as exc:
                            logger.error("Error processing IPC message %s: %s", file_path.name, exc)
                            err_dir = ipc_base_dir / "errors"
                            err_dir.mkdir(parents=True, exist_ok=True)
                            file_path.replace(err_dir / f"{source_group}-{file_path.name}")

                if tasks_dir.exists():
                    for file_path in [p for p in tasks_dir.iterdir() if p.suffix == ".json"]:
                        try:
                            data = json.loads(file_path.read_text(encoding="utf-8"))
                            await process_task_ipc(data, source_group, is_main, deps)
                            file_path.unlink(missing_ok=True)
                        except Exception as exc:
                            logger.error("Error processing IPC task %s: %s", file_path.name, exc)
                            err_dir = ipc_base_dir / "errors"
                            err_dir.mkdir(parents=True, exist_ok=True)
                            file_path.replace(err_dir / f"{source_group}-{file_path.name}")

            await asyncio.sleep(IPC_POLL_INTERVAL)

    _watcher_task = asyncio.create_task(_loop())
    logger.info("IPC watcher started (per-group namespaces)")


def stop_ipc_watcher() -> None:
    global _watcher_task
    if _watcher_task and not _watcher_task.done():
        _watcher_task.cancel()
    _watcher_task = None
