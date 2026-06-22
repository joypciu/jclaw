"""Python orchestrator runtime for J Claw.

This is a clean-room Python implementation of the runtime loop.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import signal
from http.server import HTTPServer
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .channels.registry import (
    ChannelOpts,
    get_channel_factory,
    get_registered_channel_names,
)
from .config import (
    ASSISTANT_NAME,
    CREDENTIAL_PROXY_PORT,
    IDLE_TIMEOUT,
    POLL_INTERVAL,
    TIMEZONE,
    TRIGGER_PATTERN,
)
from .container_runner import (
    AvailableGroup,
    ContainerInput,
    ContainerOutput,
    run_container_agent,
    write_groups_snapshot,
    write_tasks_snapshot,
)
from .container_runtime import (
    PROXY_BIND_HOST,
    cleanup_orphans,
    ensure_container_runtime_running,
)
from .credential_proxy import start_credential_proxy
from .db import (
    get_all_chats,
    get_all_registered_groups,
    get_all_sessions,
    get_all_tasks,
    get_messages_since,
    get_new_messages,
    get_router_state,
    init_database,
    set_registered_group,
    set_router_state,
    set_session,
    store_chat_metadata,
    store_message,
)
from .group_folder import resolve_group_folder_path
from .group_queue import GroupQueue
from .ipc import AvailableGroup as IpcAvailableGroup
from .ipc import start_ipc_watcher, stop_ipc_watcher
from .logger import logger
from .remote_control import (
    restore_remote_control,
    start_remote_control,
    stop_remote_control,
)
from .router import find_channel, format_messages, format_outbound
from .sender_allowlist import (
    is_sender_allowed,
    is_trigger_allowed,
    load_sender_allowlist,
    should_drop_message,
)
from .task_scheduler import (
    SchedulerDependencies,
    TaskStreamOutput,
    start_scheduler_loop,
    stop_scheduler_loop,
)
from .schema import Channel, NewMessage, RegisteredGroup, ScheduledTask
from .feature_flags import is_enabled
from .hooks import emit
from .context_guard import truncate_prompt, needs_web_fallback, web_search_fallback, build_web_fallback_prompt
from .memory import update_group_memory


class _ScheduledOutput:
    def __init__(self, status: Optional[str], result: Optional[str], error: Optional[str]) -> None:
        self.status = status
        self.result = result
        self.error = error


class _IpcDepsImpl:
    def __init__(self, orchestrator: "JClawOrchestrator") -> None:
        self._o = orchestrator

    async def send_message(self, jid: str, text: str) -> None:
        await self._o._send_message(jid, text)

    def registered_groups(self) -> dict[str, RegisteredGroup]:
        return self._o.registered_groups

    def register_group(self, jid: str, group: RegisteredGroup) -> None:
        self._o.register_group(jid, group)

    async def sync_groups(self, force: bool) -> None:
        await self._o._sync_groups(force)

    def get_available_groups(self) -> list[IpcAvailableGroup]:
        groups = self._o.get_available_groups()
        return [
            IpcAvailableGroup(
                jid=g.jid,
                name=g.name,
                last_activity=g.last_activity,
                is_registered=g.is_registered,
            )
            for g in groups
        ]

    def write_groups_snapshot(
        self,
        group_folder: str,
        is_main: bool,
        available_groups: list[IpcAvailableGroup],
        registered_jids: set[str],
    ) -> None:
        write_groups_snapshot(
            group_folder,
            is_main,
            [
                AvailableGroup(
                    jid=g.jid,
                    name=g.name,
                    last_activity=g.last_activity,
                    is_registered=g.is_registered,
                )
                for g in available_groups
            ],
            registered_jids,
        )

    def on_tasks_changed(self) -> None:
        self._o._on_tasks_changed()


class JClawOrchestrator:
    def __init__(
        self,
        *,
        allow_no_channels: bool = False,
        channel_modules: Optional[list[str]] = None,
    ) -> None:
        self.allow_no_channels = allow_no_channels
        self.channel_modules = channel_modules or []

        self.last_timestamp = ""
        self.sessions: dict[str, str] = {}
        self.registered_groups: dict[str, RegisteredGroup] = {}
        self.last_agent_timestamp: dict[str, str] = {}

        self.channels: list[Channel] = []
        self.queue = GroupQueue()
        self.queue.set_process_messages_fn(self.process_group_messages)

        self._running = False
        self._stop_event = asyncio.Event()
        self._proxy_server: HTTPServer | None = None

    def _load_channel_modules(self) -> None:
        modules = list(self.channel_modules)

        # Allow runtime channel plugin loading by environment.
        raw = __import__("os").environ.get("JCLAW_CHANNEL_MODULES", "")
        if raw.strip():
            modules.extend([m.strip() for m in raw.split(",") if m.strip()])

        # Import channel barrel first (safe even if empty).
        for mod_name in ["src.channels.index", ".channels.index"] + modules:
            try:
                if mod_name.startswith("."):
                    importlib.import_module(mod_name, package=__package__)
                else:
                    importlib.import_module(mod_name)
            except Exception as exc:
                logger.warning("Failed to load channel module %s: %s", mod_name, exc)

    def load_state(self) -> None:
        self.last_timestamp = get_router_state("last_timestamp") or ""
        agent_ts = get_router_state("last_agent_timestamp")
        try:
            self.last_agent_timestamp = json.loads(agent_ts) if agent_ts else {}
        except Exception:
            logger.warning("Corrupted last_agent_timestamp in DB, resetting")
            self.last_agent_timestamp = {}

        self.sessions = get_all_sessions()
        self.registered_groups = get_all_registered_groups()
        logger.info("State loaded (groups=%s)", len(self.registered_groups))

    def save_state(self) -> None:
        set_router_state("last_timestamp", self.last_timestamp)
        set_router_state("last_agent_timestamp", json.dumps(self.last_agent_timestamp))

    def register_group(self, jid: str, group: RegisteredGroup) -> None:
        try:
            group_dir = resolve_group_folder_path(group.folder)
        except Exception as exc:
            logger.warning(
                "Rejecting group registration with invalid folder jid=%s folder=%s err=%s",
                jid,
                group.folder,
                exc,
            )
            return

        self.registered_groups[jid] = group
        set_registered_group(jid, group)
        (group_dir / "logs").mkdir(parents=True, exist_ok=True)
        logger.info("Group registered jid=%s name=%s folder=%s", jid, group.name, group.folder)

    def get_available_groups(self) -> list[AvailableGroup]:
        chats = get_all_chats()
        registered = set(self.registered_groups.keys())

        out: list[AvailableGroup] = []
        for c in chats:
            if c.get("jid") == "__group_sync__":
                continue
            if not c.get("is_group"):
                continue
            out.append(
                AvailableGroup(
                    jid=str(c.get("jid", "")),
                    name=str(c.get("name", "")),
                    last_activity=str(c.get("last_message_time", "")),
                    is_registered=str(c.get("jid", "")) in registered,
                )
            )
        return out

    async def process_group_messages(self, chat_jid: str) -> bool:
        group = self.registered_groups.get(chat_jid)
        if group is None:
            return True

        channel = find_channel(self.channels, chat_jid)
        if channel is None:
            logger.warning("No channel owns JID, skipping messages chat=%s", chat_jid)
            return True

        is_main_group = bool(group.is_main)
        since_timestamp = self.last_agent_timestamp.get(chat_jid, "")
        missed_messages = get_messages_since(chat_jid, since_timestamp, ASSISTANT_NAME)

        if not missed_messages:
            return True

        if (not is_main_group) and group.requires_trigger is not False:
            allowlist_cfg = load_sender_allowlist()
            has_trigger = any(
                TRIGGER_PATTERN.match(m.content.strip())
                and (m.is_from_me or is_trigger_allowed(chat_jid, m.sender, allowlist_cfg))
                for m in missed_messages
            )
            if not has_trigger:
                return True

        prompt = format_messages(missed_messages, TIMEZONE)

        previous_cursor = self.last_agent_timestamp.get(chat_jid, "")
        self.last_agent_timestamp[chat_jid] = missed_messages[-1].timestamp
        self.save_state()

        idle_timer: asyncio.Task[None] | None = None

        async def _reset_idle_timer() -> None:
            nonlocal idle_timer
            if idle_timer is not None:
                idle_timer.cancel()

            async def _close_later() -> None:
                await asyncio.sleep(IDLE_TIMEOUT / 1000)
                self.queue.close_stdin(chat_jid)

            idle_timer = asyncio.create_task(_close_later())

        if hasattr(channel, "set_typing"):
            try:
                await channel.set_typing(chat_jid, True)
            except Exception:
                pass

        had_error = False
        output_sent_to_user = False

        async def on_output(result: ContainerOutput) -> None:
            nonlocal had_error, output_sent_to_user
            if result.result:
                raw = result.result
                text = format_outbound(raw)
                if text:
                    await channel.send_message(chat_jid, text)
                    output_sent_to_user = True
                await _reset_idle_timer()

            if result.status == "success":
                self.queue.notify_idle(chat_jid)
            if result.status == "error":
                had_error = True

        output = await self.run_agent(group, prompt, chat_jid, on_output)

        if hasattr(channel, "set_typing"):
            try:
                await channel.set_typing(chat_jid, False)
            except Exception:
                pass

        if idle_timer is not None:
            idle_timer.cancel()

        if output == "error" or had_error:
            if output_sent_to_user:
                logger.warning("Agent error after output sent, skipping rollback group=%s", group.name)
                return True

            self.last_agent_timestamp[chat_jid] = previous_cursor
            self.save_state()
            logger.warning("Agent error, rolled back message cursor group=%s", group.name)
            return False

        return True

    async def run_agent(
        self,
        group: RegisteredGroup,
        prompt: str,
        chat_jid: str,
        on_output: Optional[Callable[[ContainerOutput], Awaitable[None]]] = None,
        *,
        is_scheduled_task: bool = False,
    ) -> str:
        await emit("before_agent_run", {
            "group_name": group.name,
            "group_folder": group.folder,
            "chat_jid": chat_jid,
            "is_scheduled": is_scheduled_task,
        })
        is_main = bool(group.is_main)
        session_id = self.sessions.get(group.folder)

        tasks = get_all_tasks()
        write_tasks_snapshot(
            group.folder,
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

        write_groups_snapshot(
            group.folder,
            is_main,
            self.get_available_groups(),
            set(self.registered_groups.keys()),
        )

        async def wrapped_on_output(output: ContainerOutput) -> None:
            if output.new_session_id:
                self.sessions[group.folder] = output.new_session_id
                set_session(group.folder, output.new_session_id)
            if output.result:
                await emit("agent_output", {
                    "group_name": group.name,
                    "chat_jid": chat_jid,
                    "text": output.result,
                    "is_final": output.status in ("success", "error"),
                })
            if on_output is not None:
                await on_output(output)

        # Context guardrail — truncate before sending if prompt exceeds context window
        safe_prompt = truncate_prompt(prompt)

        try:
            output = await run_container_agent(
                group,
                ContainerInput(
                    prompt=safe_prompt,
                    session_id=session_id,
                    group_folder=group.folder,
                    chat_jid=chat_jid,
                    is_main=is_main,
                    is_scheduled_task=is_scheduled_task,
                    assistant_name=ASSISTANT_NAME,
                ),
                lambda proc, container_name: self.queue.register_process(
                    chat_jid,
                    proc,
                    container_name,
                    group.folder,
                ),
                wrapped_on_output if on_output else None,
            )

            if output.new_session_id:
                self.sessions[group.folder] = output.new_session_id
                set_session(group.folder, output.new_session_id)

            if output.status == "error":
                await emit("after_agent_run", {
                    "group_name": group.name,
                    "chat_jid": chat_jid,
                    "status": "error",
                    "error": output.error,
                })
                logger.error("Container agent error group=%s err=%s", group.name, output.error)
                return "error"

            # Web search fallback (Hermes pattern):
            # If agent returned TOOLS_UNAVAILABLE, retry with host-browser result injected.
            if output.result and needs_web_fallback(output.result):
                logger.info("Web tools unavailable — attempting host-browser fallback (group=%s)", group.name)
                search_result = await web_search_fallback(safe_prompt)
                if search_result:
                    fallback_prompt = build_web_fallback_prompt(safe_prompt, search_result)
                    output = await run_container_agent(
                        group,
                        ContainerInput(
                            prompt=fallback_prompt,
                            session_id=self.sessions.get(group.folder),
                            group_folder=group.folder,
                            chat_jid=chat_jid,
                            is_main=is_main,
                            is_scheduled_task=is_scheduled_task,
                            assistant_name=ASSISTANT_NAME,
                        ),
                        lambda proc, container_name: self.queue.register_process(
                            chat_jid, proc, container_name, group.folder,
                        ),
                        wrapped_on_output if on_output else None,
                    )
                    if output.new_session_id:
                        self.sessions[group.folder] = output.new_session_id
                        set_session(group.folder, output.new_session_id)

            # Hindsight memory (oh-my-pi pattern):
            # After a successful run, update the group's CLAUDE.md with a dated episode.
            group_dir = resolve_group_folder_path(group.folder)
            try:
                update_group_memory(group_dir, result_summary=output.result)
            except Exception as mem_exc:
                logger.debug("Hindsight memory update failed (non-fatal): %s", mem_exc)

            await emit("after_agent_run", {
                "group_name": group.name,
                "chat_jid": chat_jid,
                "status": "success",
                "error": None,
            })
            return "success"
        except Exception as exc:
            logger.error("Agent error group=%s err=%s", group.name, exc)
            await emit("after_agent_run", {
                "group_name": group.name,
                "chat_jid": chat_jid,
                "status": "error",
                "error": str(exc),
            })
            return "error"

    async def _start_message_loop(self) -> None:
        logger.info("J Claw running (trigger: @%s)", ASSISTANT_NAME)

        while not self._stop_event.is_set():
            try:
                jids = list(self.registered_groups.keys())
                messages, new_ts = get_new_messages(jids, self.last_timestamp, ASSISTANT_NAME)

                if messages:
                    self.last_timestamp = new_ts
                    self.save_state()

                    by_group: dict[str, list[NewMessage]] = {}
                    for msg in messages:
                        by_group.setdefault(msg.chat_jid, []).append(msg)

                    for chat_jid, group_messages in by_group.items():
                        group = self.registered_groups.get(chat_jid)
                        if group is None:
                            continue

                        channel = find_channel(self.channels, chat_jid)
                        if channel is None:
                            continue

                        needs_trigger = (not bool(group.is_main)) and group.requires_trigger is not False
                        if needs_trigger:
                            cfg = load_sender_allowlist()
                            has_trigger = any(
                                TRIGGER_PATTERN.match(m.content.strip())
                                and (m.is_from_me or is_trigger_allowed(chat_jid, m.sender, cfg))
                                for m in group_messages
                            )
                            if not has_trigger:
                                continue

                        all_pending = get_messages_since(
                            chat_jid,
                            self.last_agent_timestamp.get(chat_jid, ""),
                            ASSISTANT_NAME,
                        )
                        messages_to_send = all_pending if all_pending else group_messages
                        formatted = format_messages(messages_to_send, TIMEZONE)

                        if self.queue.send_message(chat_jid, formatted):
                            self.last_agent_timestamp[chat_jid] = messages_to_send[-1].timestamp
                            self.save_state()
                            if hasattr(channel, "set_typing"):
                                try:
                                    await channel.set_typing(chat_jid, True)
                                except Exception:
                                    pass
                        else:
                            await self.queue.enqueue_message_check(chat_jid)

            except Exception as exc:
                logger.error("Error in message loop: %s", exc)

            await asyncio.sleep(POLL_INTERVAL)

    async def _recover_pending_messages(self) -> None:
        for chat_jid, group in self.registered_groups.items():
            pending = get_messages_since(
                chat_jid,
                self.last_agent_timestamp.get(chat_jid, ""),
                ASSISTANT_NAME,
            )
            if pending:
                logger.info("Recovery found unprocessed messages group=%s count=%s", group.name, len(pending))
                await self.queue.enqueue_message_check(chat_jid)

    async def _handle_remote_control(self, command: str, chat_jid: str, msg: NewMessage) -> None:
        group = self.registered_groups.get(chat_jid)
        if not group or not group.is_main:
            logger.warning("Remote control rejected: not main group chat=%s", chat_jid)
            return

        channel = find_channel(self.channels, chat_jid)
        if channel is None:
            return

        if command == "/remote-control":
            result = start_remote_control(msg.sender, chat_jid, str(Path.cwd()))
            if result.get("ok"):
                await channel.send_message(chat_jid, str(result.get("url", "")))
            else:
                await channel.send_message(chat_jid, f"Remote Control failed: {result.get('error', 'Unknown error')}")
            return

        result = stop_remote_control()
        if result.get("ok"):
            await channel.send_message(chat_jid, "Remote Control session ended.")
        else:
            await channel.send_message(chat_jid, str(result.get("error", "Unknown error")))

    async def _connect_channels(self) -> None:
        self._load_channel_modules()

        def on_message(chat_jid: str, msg: NewMessage) -> None:
            trimmed = msg.content.strip()
            if trimmed in {"/remote-control", "/remote-control-end"}:
                asyncio.create_task(self._handle_remote_control(trimmed, chat_jid, msg))
                return

            if (not msg.is_from_me) and (not msg.is_bot_message) and chat_jid in self.registered_groups:
                cfg = load_sender_allowlist()
                if should_drop_message(chat_jid, cfg) and (not is_sender_allowed(chat_jid, msg.sender, cfg)):
                    if cfg.log_denied:
                        logger.debug("sender-allowlist: dropping message chat=%s sender=%s", chat_jid, msg.sender)
                    return

            store_message(msg)
            asyncio.get_event_loop().call_soon_threadsafe(
                lambda m=msg: asyncio.ensure_future(emit("message_received", {
                    "chat_jid": chat_jid,
                    "sender": m.sender,
                    "content": m.content,
                    "timestamp": m.timestamp,
                    "is_from_me": m.is_from_me,
                }))
            )

        def on_chat_metadata(
            chat_jid: str,
            timestamp: str,
            name: Optional[str] = None,
            channel: Optional[str] = None,
            is_group: Optional[bool] = None,
        ) -> None:
            store_chat_metadata(chat_jid, timestamp, name, channel, is_group)

        opts = ChannelOpts(
            on_message=on_message,
            on_chat_metadata=on_chat_metadata,
            registered_groups=lambda: self.registered_groups,
        )

        for channel_name in get_registered_channel_names():
            factory = get_channel_factory(channel_name)
            if factory is None:
                continue
            try:
                channel = factory(opts)
            except Exception as exc:
                logger.warning("Channel factory failed for %s: %s", channel_name, exc)
                continue

            if channel is None:
                logger.warning("Channel %s installed but credentials missing — skipping", channel_name)
                continue

            self.channels.append(channel)

        # ── Parallel connect (all channels connect concurrently) ──────────
        async def _connect_one(ch: Channel) -> None:
            try:
                await ch.connect()
                await emit("channel_connected", {"channel_name": getattr(ch, 'name', type(ch).__name__)})
            except Exception as exc:
                await emit("channel_disconnected", {
                    "channel_name": getattr(ch, 'name', type(ch).__name__),
                    "error": str(exc),
                })
                raise

        results = await asyncio.gather(
            *[_connect_one(ch) for ch in self.channels],
            return_exceptions=True,
        )
        failed = [r for r in results if isinstance(r, BaseException)]
        for exc in failed:
            logger.warning("Channel connect failed: %s", exc)

        if (not self.channels) and (not self.allow_no_channels):
            raise RuntimeError(
                "No channels connected. Configure a channel or run with allow_no_channels enabled."
            )

    async def _sync_groups(self, force: bool) -> None:
        for ch in self.channels:
            if hasattr(ch, "sync_groups"):
                try:
                    await ch.sync_groups(force)
                except Exception as exc:
                    logger.warning("sync_groups failed for %s: %s", getattr(ch, "name", "unknown"), exc)

    def _on_tasks_changed(self) -> None:
        tasks = get_all_tasks()
        task_rows = [
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
        ]
        for group in self.registered_groups.values():
            write_tasks_snapshot(group.folder, bool(group.is_main), task_rows)

    async def _send_message(self, jid: str, text: str) -> None:
        channel = find_channel(self.channels, jid)
        if channel is None:
            raise RuntimeError(f"No channel for JID: {jid}")
        await channel.send_message(jid, text)

    async def _send_outbound(self, jid: str, raw_text: str) -> None:
        channel = find_channel(self.channels, jid)
        if channel is None:
            logger.warning("No channel owns JID, cannot send message jid=%s", jid)
            return
        text = format_outbound(raw_text)
        if text:
            await channel.send_message(jid, text)

    async def _run_scheduled_agent(
        self,
        group: RegisteredGroup,
        task: ScheduledTask,
        session_id: Optional[str],
        on_stream: Callable[[TaskStreamOutput], Awaitable[None]],
    ) -> TaskStreamOutput:
        is_main = bool(group.is_main)
        output = await run_container_agent(
            group,
            ContainerInput(
                prompt=task.prompt,
                session_id=session_id,
                group_folder=task.group_folder,
                chat_jid=task.chat_jid,
                is_main=is_main,
                is_scheduled_task=True,
                assistant_name=ASSISTANT_NAME,
            ),
            lambda proc, container_name: self.queue.register_process(
                task.chat_jid,
                proc,
                container_name,
                task.group_folder,
            ),
            self._wrap_scheduled_on_stream(on_stream),
        )
        return _ScheduledOutput(
            status=output.status,
            result=output.result,
            error=output.error,
        )

    @staticmethod
    def _wrap_scheduled_on_stream(
        callback: Callable[[TaskStreamOutput], Awaitable[None]],
    ) -> Callable[[ContainerOutput], Awaitable[None]]:
        async def _wrapped(output: ContainerOutput) -> None:
            await callback(
                _ScheduledOutput(
                    status=output.status,
                    result=output.result,
                    error=output.error,
                )
            )

        return _wrapped

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        def _set_stop() -> None:
            self._stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _set_stop)
            except NotImplementedError:
                signal.signal(sig, lambda _s, _f: loop.call_soon_threadsafe(_set_stop))

    async def start(self) -> None:
        self._running = True
        self._stop_event.clear()

        ensure_container_runtime_running()
        cleanup_orphans()

        init_database()
        self.load_state()
        restore_remote_control()

        self._proxy_server = start_credential_proxy(CREDENTIAL_PROXY_PORT, PROXY_BIND_HOST)

        await self._connect_channels()

        scheduler_deps = SchedulerDependencies(
            registered_groups=lambda: self.registered_groups,
            get_sessions=lambda: self.sessions,
            queue=self.queue,
            run_scheduled_agent=self._run_scheduled_agent,
            send_message=self._send_outbound,
            write_tasks_snapshot=write_tasks_snapshot,
        )
        # Gate optional subsystems behind feature flags
        if is_enabled("scheduler"):
            start_scheduler_loop(scheduler_deps)
        else:
            logger.info("Scheduler disabled (JCLAW_FEATURES)")

        if is_enabled("ipc"):
            start_ipc_watcher(_IpcDepsImpl(self))
        else:
            logger.info("IPC watcher disabled (JCLAW_FEATURES)")

        await self._recover_pending_messages()

        await emit("startup_complete", {
            "channel_count": len(self.channels),
            "group_count": len(self.registered_groups),
        })

        self._install_signal_handlers()
        await self._start_message_loop()

    async def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        stop_ipc_watcher()
        stop_scheduler_loop()

        await self.queue.shutdown()

        for ch in self.channels:
            try:
                await ch.disconnect()
            except Exception as exc:
                logger.warning("Channel disconnect failed for %s: %s", getattr(ch, "name", "unknown"), exc)
        self.channels.clear()

        if self._proxy_server is not None:
            self._proxy_server.shutdown()
            self._proxy_server.server_close()
            self._proxy_server = None


async def run_orchestrator(
    *,
    allow_no_channels: bool = False,
    channel_modules: Optional[list[str]] = None,
) -> None:
    orchestrator = JClawOrchestrator(
        allow_no_channels=allow_no_channels,
        channel_modules=channel_modules,
    )

    try:
        await orchestrator.start()
    finally:
        await orchestrator.stop()
