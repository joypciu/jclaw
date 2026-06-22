"""Shared dataclasses and protocols for J Claw."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, runtime_checkable


@dataclass
class AdditionalMount:
    host_path: str
    container_path: Optional[str] = None
    readonly: bool = True


@dataclass
class AllowedRoot:
    path: str
    allow_read_write: bool = False
    description: Optional[str] = None


@dataclass
class MountAllowlist:
    allowed_roots: list[AllowedRoot] = field(default_factory=list)
    blocked_patterns: list[str] = field(default_factory=list)
    non_main_read_only: bool = True


@dataclass
class ContainerConfig:
    additional_mounts: list[AdditionalMount] = field(default_factory=list)
    timeout: Optional[int] = None


@dataclass
class RegisteredGroup:
    name: str
    folder: str
    trigger: str
    added_at: str
    container_config: Optional[ContainerConfig] = None
    requires_trigger: Optional[bool] = None
    is_main: Optional[bool] = None


@dataclass
class NewMessage:
    id: str
    chat_jid: str
    sender: str
    sender_name: str
    content: str
    timestamp: str
    is_from_me: bool = False
    is_bot_message: bool = False


@dataclass
class ScheduledTask:
    id: str
    group_folder: str
    chat_jid: str
    prompt: str
    schedule_type: str  # 'cron' | 'interval' | 'once'
    schedule_value: str
    context_mode: str  # 'group' | 'isolated'
    next_run: Optional[str]
    last_run: Optional[str]
    last_result: Optional[str]
    status: str  # 'active' | 'paused' | 'completed'
    created_at: str


@dataclass
class TaskRunLog:
    task_id: str
    run_at: str
    duration_ms: int
    status: str
    result: Optional[str]
    error: Optional[str]


# --- Channel abstraction ---

@runtime_checkable
class Channel(Protocol):
    name: str

    async def connect(self) -> None: ...
    async def send_message(self, jid: str, text: str) -> None: ...
    def is_connected(self) -> bool: ...
    def owns_jid(self, jid: str) -> bool: ...
    async def disconnect(self) -> None: ...
    async def set_typing(self, jid: str, is_typing: bool) -> None: ...  # optional
    async def sync_groups(self, force: bool) -> None: ...  # optional


OnInboundMessage = Callable[[str, NewMessage], None]
OnChatMetadata = Callable[[str, str, Optional[str], Optional[str], Optional[bool]], None]
