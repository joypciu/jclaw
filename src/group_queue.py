"""Per-group execution queue with global concurrency controls.

Python port of the TypeScript GroupQueue runtime primitive.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .config import DATA_DIR, MAX_CONCURRENT_CONTAINERS
from .logger import logger


ProcessMessagesFn = Callable[[str], Awaitable[bool]]
TaskFn = Callable[[], Awaitable[None]]

_MAX_RETRIES = 5
_BASE_RETRY_S = 5.0


@dataclass
class QueuedTask:
    id: str
    group_jid: str
    fn: TaskFn


@dataclass
class GroupState:
    active: bool = False
    idle_waiting: bool = False
    is_task_container: bool = False
    running_task_id: Optional[str] = None
    pending_messages: bool = False
    pending_tasks: list[QueuedTask] = field(default_factory=list)
    process: object | None = None
    container_name: Optional[str] = None
    group_folder: Optional[str] = None
    retry_count: int = 0


class GroupQueue:
    def __init__(self) -> None:
        self._groups: dict[str, GroupState] = {}
        self._active_count = 0
        self._waiting_groups: list[str] = []
        self._process_messages_fn: Optional[ProcessMessagesFn] = None
        self._shutting_down = False
        self._retry_tasks: set[asyncio.Task[None]] = set()

    def _get_group(self, group_jid: str) -> GroupState:
        state = self._groups.get(group_jid)
        if state is None:
            state = GroupState()
            self._groups[group_jid] = state
        return state

    def set_process_messages_fn(self, fn: ProcessMessagesFn) -> None:
        self._process_messages_fn = fn

    async def enqueue_message_check(self, group_jid: str) -> None:
        if self._shutting_down:
            return

        state = self._get_group(group_jid)

        if state.active:
            state.pending_messages = True
            logger.debug("Container active, message queued for %s", group_jid)
            return

        if self._active_count >= MAX_CONCURRENT_CONTAINERS:
            state.pending_messages = True
            if group_jid not in self._waiting_groups:
                self._waiting_groups.append(group_jid)
            logger.debug(
                "At concurrency limit, message queued for %s (active=%s)",
                group_jid,
                self._active_count,
            )
            return

        await self._run_for_group(group_jid, "messages")

    async def enqueue_task(self, group_jid: str, task_id: str, fn: TaskFn) -> None:
        if self._shutting_down:
            return

        state = self._get_group(group_jid)

        if state.running_task_id == task_id:
            logger.debug("Task %s already running for %s, skipping", task_id, group_jid)
            return
        if any(t.id == task_id for t in state.pending_tasks):
            logger.debug("Task %s already queued for %s, skipping", task_id, group_jid)
            return

        task = QueuedTask(id=task_id, group_jid=group_jid, fn=fn)

        if state.active:
            state.pending_tasks.append(task)
            if state.idle_waiting:
                self.close_stdin(group_jid)
            logger.debug("Container active, task queued (%s:%s)", group_jid, task_id)
            return

        if self._active_count >= MAX_CONCURRENT_CONTAINERS:
            state.pending_tasks.append(task)
            if group_jid not in self._waiting_groups:
                self._waiting_groups.append(group_jid)
            logger.debug(
                "At concurrency limit, task queued (%s:%s, active=%s)",
                group_jid,
                task_id,
                self._active_count,
            )
            return

        await self._run_task(group_jid, task)

    def register_process(
        self,
        group_jid: str,
        proc: object,
        container_name: str,
        group_folder: Optional[str] = None,
    ) -> None:
        state = self._get_group(group_jid)
        state.process = proc
        state.container_name = container_name
        if group_folder:
            state.group_folder = group_folder

    def notify_idle(self, group_jid: str) -> None:
        state = self._get_group(group_jid)
        state.idle_waiting = True
        if state.pending_tasks:
            self.close_stdin(group_jid)

    def send_message(self, group_jid: str, text: str) -> bool:
        state = self._get_group(group_jid)
        if (not state.active) or (not state.group_folder) or state.is_task_container:
            return False

        state.idle_waiting = False
        input_dir = Path(DATA_DIR) / "ipc" / state.group_folder / "input"
        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{int(time.time() * 1000)}-{str(time.time_ns())[-6:]}.json"
            filepath = input_dir / filename
            tmp = filepath.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"type": "message", "text": text}), encoding="utf-8")
            tmp.replace(filepath)
            return True
        except Exception:
            return False

    def close_stdin(self, group_jid: str) -> None:
        state = self._get_group(group_jid)
        if (not state.active) or (not state.group_folder):
            return

        input_dir = Path(DATA_DIR) / "ipc" / state.group_folder / "input"
        try:
            input_dir.mkdir(parents=True, exist_ok=True)
            (input_dir / "_close").write_text("", encoding="utf-8")
        except Exception:
            return

    async def _run_for_group(self, group_jid: str, reason: str) -> None:
        state = self._get_group(group_jid)
        state.active = True
        state.idle_waiting = False
        state.is_task_container = False
        state.pending_messages = False
        self._active_count += 1

        logger.debug(
            "Starting container for group %s (reason=%s, active=%s)",
            group_jid,
            reason,
            self._active_count,
        )

        try:
            if self._process_messages_fn is not None:
                success = await self._process_messages_fn(group_jid)
                if success:
                    state.retry_count = 0
                else:
                    await self._schedule_retry(group_jid, state)
        except Exception as exc:
            logger.error("Error processing messages for %s: %s", group_jid, exc)
            await self._schedule_retry(group_jid, state)
        finally:
            state.active = False
            state.process = None
            state.container_name = None
            state.group_folder = None
            self._active_count -= 1
            await self._drain_group(group_jid)

    async def _run_task(self, group_jid: str, task: QueuedTask) -> None:
        state = self._get_group(group_jid)
        state.active = True
        state.idle_waiting = False
        state.is_task_container = True
        state.running_task_id = task.id
        self._active_count += 1

        logger.debug(
            "Running queued task %s for %s (active=%s)",
            task.id,
            group_jid,
            self._active_count,
        )

        try:
            await task.fn()
        except Exception as exc:
            logger.error("Error running task %s for %s: %s", task.id, group_jid, exc)
        finally:
            state.active = False
            state.is_task_container = False
            state.running_task_id = None
            state.process = None
            state.container_name = None
            state.group_folder = None
            self._active_count -= 1
            await self._drain_group(group_jid)

    async def _schedule_retry(self, group_jid: str, state: GroupState) -> None:
        state.retry_count += 1
        if state.retry_count > _MAX_RETRIES:
            logger.error(
                "Max retries exceeded for %s, dropping queued messages until next incoming",
                group_jid,
            )
            state.retry_count = 0
            return

        delay_s = _BASE_RETRY_S * (2 ** (state.retry_count - 1))
        logger.info(
            "Scheduling retry for %s (retry=%s, delay=%.1fs)",
            group_jid,
            state.retry_count,
            delay_s,
        )

        async def _retry_later() -> None:
            await asyncio.sleep(delay_s)
            if not self._shutting_down:
                await self.enqueue_message_check(group_jid)

        task = asyncio.create_task(_retry_later())
        self._retry_tasks.add(task)
        task.add_done_callback(lambda t: self._retry_tasks.discard(t))

    async def _drain_group(self, group_jid: str) -> None:
        if self._shutting_down:
            return

        state = self._get_group(group_jid)

        if state.pending_tasks:
            task = state.pending_tasks.pop(0)
            await self._run_task(group_jid, task)
            return

        if state.pending_messages:
            await self._run_for_group(group_jid, "drain")
            return

        await self._drain_waiting()

    async def _drain_waiting(self) -> None:
        while self._waiting_groups and self._active_count < MAX_CONCURRENT_CONTAINERS:
            next_jid = self._waiting_groups.pop(0)
            state = self._get_group(next_jid)

            if state.pending_tasks:
                task = state.pending_tasks.pop(0)
                await self._run_task(next_jid, task)
            elif state.pending_messages:
                await self._run_for_group(next_jid, "drain")

    async def shutdown(self) -> None:
        self._shutting_down = True
        for task in list(self._retry_tasks):
            task.cancel()
        self._retry_tasks.clear()

        active_containers: list[str] = []
        for state in self._groups.values():
            if state.process and state.container_name:
                active_containers.append(state.container_name)

        logger.info(
            "GroupQueue shutting down (active=%s, detached=%s)",
            self._active_count,
            active_containers,
        )
