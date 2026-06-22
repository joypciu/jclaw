"""Local console channel for credential-free testing and development."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

from ..types import NewMessage
from .registry import ChannelOpts, register_channel


class ConsoleChannel:
    name = "console"

    def __init__(self, opts: ChannelOpts) -> None:
        self._opts = opts
        self._connected = False
        self._reader_task: asyncio.Task[None] | None = None
        self._jid = os.environ.get("JCLAW_CONSOLE_JID", "console:main")
        self._chat_name = os.environ.get("JCLAW_CONSOLE_NAME", "Console Chat")

    def owns_jid(self, jid: str) -> bool:
        return jid == self._jid

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True
        now = datetime.now(timezone.utc).isoformat()
        self._opts.on_chat_metadata(self._jid, now, self._chat_name, "console", True)
        self._reader_task = asyncio.create_task(self._read_loop())
        print("[console-channel] connected. Type messages and press Enter.")
        print(f"[console-channel] chat jid: {self._jid}")

    async def disconnect(self) -> None:
        self._connected = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None

    async def send_message(self, jid: str, text: str) -> None:
        if jid != self._jid:
            return
        sys.stdout.write(f"\nJClaw> {text}\n")
        sys.stdout.flush()

    async def set_typing(self, jid: str, is_typing: bool) -> None:
        if jid != self._jid:
            return
        if is_typing:
            sys.stdout.write("\nJClaw is typing...\n")
            sys.stdout.flush()

    async def sync_groups(self, force: bool) -> None:  # noqa: ARG002
        return

    async def _read_loop(self) -> None:
        while self._connected:
            try:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    await asyncio.sleep(0.1)
                    continue
                text = line.strip()
                if not text:
                    continue

                msg = NewMessage(
                    id=str(uuid.uuid4()),
                    chat_jid=self._jid,
                    sender="console:user",
                    sender_name="Console User",
                    content=text,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    is_from_me=False,
                    is_bot_message=False,
                )
                self._opts.on_message(self._jid, msg)
                self._opts.on_chat_metadata(
                    self._jid,
                    msg.timestamp,
                    self._chat_name,
                    "console",
                    True,
                )
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(0.1)


def _console_factory(opts: ChannelOpts):
    enabled = os.environ.get("JCLAW_ENABLE_CONSOLE_CHANNEL", "").lower() in {"1", "true", "yes"}
    if not enabled:
        return None
    return ConsoleChannel(opts)


register_channel("console", _console_factory)
