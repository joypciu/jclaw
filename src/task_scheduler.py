"""Scheduled task loop and task execution helpers."""
from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional, Protocol, cast
from zoneinfo import ZoneInfo

from .config import ASSISTANT_NAME, SCHEDULER_POLL_INTERVAL, TIMEZONE
from .db import (
    get_all_tasks,
    get_due_tasks,
    get_task_by_id,
    log_task_run,
    update_task,
    update_task_after_run,
)
from .group_folder import resolve_group_folder_path
from .logger import logger
from .schema import RegisteredGroup, ScheduledTask, TaskRunLog


class QueueProtocol(Protocol):
    async def enqueue_task(self, group_jid: str, task_id: str, fn: Callable[[], Awaitable[None]]) -> None: ...
    def notify_idle(self, group_jid: str) -> None: ...
    def close_stdin(self, group_jid: str) -> None: ...


class TaskStreamOutput(Protocol):
    result: Optional[str]
    status: Optional[str]
    error: Optional[str]


RunScheduledAgentFn = Callable[
    [
        RegisteredGroup,
        ScheduledTask,
        Optional[str],
        Callable[[TaskStreamOutput], Awaitable[None]],
    ],
    Awaitable[TaskStreamOutput],
]


WriteTasksSnapshotFn = Callable[[str, bool, list[dict[str, object]]], None]


@dataclass
class SchedulerDependencies:
    registered_groups: Callable[[], dict[str, RegisteredGroup]]
    get_sessions: Callable[[], dict[str, str]]
    queue: QueueProtocol
    run_scheduled_agent: RunScheduledAgentFn
    send_message: Callable[[str, str], Awaitable[None]]
    write_tasks_snapshot: WriteTasksSnapshotFn


_scheduler_task: asyncio.Task[None] | None = None


def _get_croniter() -> Callable[..., Any] | None:
    try:
        mod = importlib.import_module("croniter")
        return cast(Callable[..., Any] | None, getattr(mod, "croniter", None))
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def compute_next_run(task: ScheduledTask) -> str | None:
    """Compute next run anchored to task schedule, avoiding interval drift."""
    if task.schedule_type == "once":
        return None

    now = _now_utc()

    if task.schedule_type == "cron":
        tz = ZoneInfo(TIMEZONE)
        base = now.astimezone(tz)
        croniter_cls = _get_croniter()
        if croniter_cls is None:
            logger.warning(
                "croniter not installed; falling back to +1 minute for task %s",
                task.id,
            )
            return (now + timedelta(minutes=1)).isoformat()

        it = croniter_cls(task.schedule_value, base)
        nxt = it.get_next(datetime)
        return nxt.astimezone(timezone.utc).isoformat()

    if task.schedule_type == "interval":
        try:
            ms = int(task.schedule_value)
        except ValueError:
            ms = 0

        if ms <= 0:
            logger.warning(
                "Invalid interval value for task %s: %s",
                task.id,
                task.schedule_value,
            )
            return (now + timedelta(minutes=1)).isoformat()

        anchor = now
        if task.next_run:
            try:
                anchor = datetime.fromisoformat(task.next_run)
                if anchor.tzinfo is None:
                    anchor = anchor.replace(tzinfo=timezone.utc)
                else:
                    anchor = anchor.astimezone(timezone.utc)
            except ValueError:
                anchor = now

        delta = timedelta(milliseconds=ms)
        nxt = anchor + delta
        while nxt <= now:
            nxt += delta
        return nxt.isoformat()

    return None


async def run_task(task: ScheduledTask, deps: SchedulerDependencies) -> None:
    start = _now_utc()

    try:
        resolve_group_folder_path(task.group_folder)
    except Exception as exc:
        error = str(exc)
        update_task(task.id, status="paused")
        logger.error("Task %s has invalid group folder %s: %s", task.id, task.group_folder, error)
        log_task_run(TaskRunLog(
            task_id=task.id,
            run_at=_now_utc().isoformat(),
            duration_ms=int((_now_utc() - start).total_seconds() * 1000),
            status="error",
            result=None,
            error=error,
        ))
        return

    groups = deps.registered_groups()
    group = next((g for g in groups.values() if g.folder == task.group_folder), None)
    if not group:
        error = f"Group not found: {task.group_folder}"
        logger.error("Task %s group lookup failed: %s", task.id, error)
        log_task_run(TaskRunLog(
            task_id=task.id,
            run_at=_now_utc().isoformat(),
            duration_ms=int((_now_utc() - start).total_seconds() * 1000),
            status="error",
            result=None,
            error=error,
        ))
        return

    is_main = bool(group.is_main)
    tasks = get_all_tasks()
    deps.write_tasks_snapshot(
        task.group_folder,
        is_main,
        [
            {
                "id": t.id,
                "groupFolder": t.group_folder,
                "prompt": t.prompt,
                "schedule_type": t.schedule_type,
                "schedule_value": t.schedule_value,
                "status": t.status,
                "next_run": t.next_run,
            }
            for t in tasks
        ],
    )

    sessions = deps.get_sessions()
    session_id = sessions.get(task.group_folder) if task.context_mode == "group" else None

    result: str | None = None
    error: str | None = None
    close_timer: asyncio.Task[None] | None = None

    async def _schedule_close() -> None:
        await asyncio.sleep(10)
        deps.queue.close_stdin(task.chat_jid)

    async def on_stream(output: TaskStreamOutput) -> None:
        nonlocal result, error, close_timer

        if output.result:
            result = output.result
            await deps.send_message(task.chat_jid, output.result)
            if close_timer is None:
                close_timer = asyncio.create_task(_schedule_close())

        if output.status == "success":
            deps.queue.notify_idle(task.chat_jid)
            if close_timer is None:
                close_timer = asyncio.create_task(_schedule_close())

        if output.status == "error":
            error = output.error or "Unknown error"

    try:
        final_output = await deps.run_scheduled_agent(group, task, session_id, on_stream)

        if final_output.status == "error":
            error = final_output.error or "Unknown error"
        elif final_output.result:
            result = final_output.result

    except Exception as exc:
        error = str(exc)
        logger.error("Task %s failed: %s", task.id, error)
    finally:
        if close_timer is not None:
            close_timer.cancel()

    duration_ms = int((_now_utc() - start).total_seconds() * 1000)

    log_task_run(TaskRunLog(
        task_id=task.id,
        run_at=_now_utc().isoformat(),
        duration_ms=duration_ms,
        status="error" if error else "success",
        result=result,
        error=error,
    ))

    next_run = compute_next_run(task)
    if error:
        summary = f"Error: {error}"
    elif result:
        summary = result[:200]
    else:
        summary = "Completed"

    update_task_after_run(task.id, next_run, summary)


def start_scheduler_loop(deps: SchedulerDependencies) -> None:
    global _scheduler_task

    if _scheduler_task and not _scheduler_task.done():
        logger.debug("Scheduler loop already running, skipping duplicate start")
        return

    logger.info("Scheduler loop started")

    async def _loop() -> None:
        while True:
            try:
                due_tasks = get_due_tasks()
                if due_tasks:
                    logger.info("Found %s due tasks", len(due_tasks))

                for task in due_tasks:
                    current = get_task_by_id(task.id)
                    if current is None or current.status != "active":
                        continue

                    await deps.queue.enqueue_task(
                        current.chat_jid,
                        current.id,
                        lambda current=current: run_task(current, deps),
                    )
            except Exception as exc:
                logger.error("Error in scheduler loop: %s", exc)

            await asyncio.sleep(SCHEDULER_POLL_INTERVAL)

    _scheduler_task = asyncio.create_task(_loop())


def stop_scheduler_loop() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None
