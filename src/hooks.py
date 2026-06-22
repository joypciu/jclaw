"""Lightweight hook / event pipeline for J Claw.

Inspired by claw-code's plugin hook pipeline and openclaude's provider layer,
this module provides a publish-subscribe event bus that lets channels, skills,
and external plugins extend jclaw behavior at well-defined lifecycle points
without modifying core code.

Design goals
────────────
• Zero overhead when no hooks are registered for an event
• Async-first: handlers can be coroutines; non-async handlers run in a thread
  pool executor so they don't block the event loop
• Fault-isolated: a crashing hook is logged and skipped; it never kills the
  orchestrator
• Ordered: hooks fire in registration order (within the same priority tier)
• Typed: each event name has a documented payload shape (plain dicts for
  forward compatibility)

Usage
-----
Register a hook in a channel plugin or skill initializer:

    from src.hooks import on

    @on("agent_output")
    async def my_listener(payload: dict) -> None:
        text = payload.get("text", "")
        ...

Emit an event from the orchestrator or container runner:

    from src.hooks import emit

    await emit("agent_output", {"group": group.name, "text": result.result})

Events reference
────────────────
message_received    — fired when a raw inbound message is stored
    keys: chat_jid, sender, content, timestamp, is_from_me

before_agent_run    — fired before a container agent starts
    keys: group_name, group_folder, chat_jid, prompt, is_scheduled

after_agent_run     — fired after a container agent finishes (success or error)
    keys: group_name, chat_jid, status ("success"|"error"), error

agent_output        — fired for each streamed output chunk from a container
    keys: group_name, chat_jid, text, is_final

channel_connected   — fired when a channel successfully connects
    keys: channel_name

channel_disconnected — fired when a channel disconnects or errors
    keys: channel_name, error

provider_selected   — fired each time the credential proxy selects an endpoint
    keys: alias, url

startup_complete    — fired once all channels are connected and the message
                      loop is about to start
    keys: channel_count, group_count
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Union

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Union[None, Awaitable[None]]]

# event_name → ordered list of (priority, handler)
_REGISTRY: dict[str, list[tuple[int, Handler]]] = defaultdict(list)

# ── Registration ──────────────────────────────────────────────────────────────

def register(event: str, handler: Handler, *, priority: int = 0) -> None:
    """Register *handler* for *event*.

    Lower priority values fire first (0 is default).  Handlers with equal
    priority fire in registration order.
    """
    _REGISTRY[event].append((priority, handler))
    _REGISTRY[event].sort(key=lambda x: x[0])


def on(event: str, *, priority: int = 0) -> Callable[[Handler], Handler]:
    """Decorator form of :func:`register`.

    Example::

        @on("agent_output")
        async def notify(payload: dict) -> None:
            ...
    """
    def _decorator(fn: Handler) -> Handler:
        register(event, fn, priority=priority)
        return fn

    return _decorator


def unregister(event: str, handler: Handler) -> None:
    """Remove a previously registered handler."""
    _REGISTRY[event] = [(p, h) for p, h in _REGISTRY[event] if h is not handler]


def clear(event: str | None = None) -> None:
    """Remove all handlers for *event*, or all handlers if *event* is None."""
    if event is None:
        _REGISTRY.clear()
    else:
        _REGISTRY.pop(event, None)


# ── Emission ──────────────────────────────────────────────────────────────────

async def emit(event: str, payload: dict[str, Any] | None = None) -> None:
    """Fire all handlers registered for *event*.

    Each handler is isolated: exceptions are caught and logged.  Sync
    handlers are run in `asyncio.get_event_loop().run_in_executor` so they
    never block the event loop.
    """
    handlers = _REGISTRY.get(event)
    if not handlers:
        return

    data = payload or {}
    loop = asyncio.get_event_loop()

    for _priority, handler in handlers:
        try:
            if inspect.iscoroutinefunction(handler):
                await handler(data)
            else:
                await loop.run_in_executor(None, handler, data)
        except Exception as exc:
            logger.error(
                "Hook error (event=%s handler=%s): %s",
                event,
                getattr(handler, "__name__", repr(handler)),
                exc,
            )


def emit_sync(event: str, payload: dict[str, Any] | None = None) -> None:
    """Fire sync-only handlers for *event* without requiring an event loop.

    Useful in module-level or thread-pool code.  Async handlers registered
    for this event are skipped with a warning.
    """
    handlers = _REGISTRY.get(event)
    if not handlers:
        return

    data = payload or {}
    for _priority, handler in handlers:
        if inspect.iscoroutinefunction(handler):
            logger.warning(
                "emit_sync: skipping async handler %s for event %s",
                getattr(handler, "__name__", repr(handler)),
                event,
            )
            continue
        try:
            handler(data)
        except Exception as exc:
            logger.error("Hook error (sync event=%s): %s", event, exc)


# ── Introspection ─────────────────────────────────────────────────────────────

def registered_events() -> list[str]:
    """Return all event names that have at least one handler."""
    return [evt for evt, handlers in _REGISTRY.items() if handlers]


def handler_count(event: str) -> int:
    """Return the number of handlers registered for *event*."""
    return len(_REGISTRY.get(event, []))
