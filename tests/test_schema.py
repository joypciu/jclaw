"""Tests for src/schema.py — dataclasses and Channel protocol."""
from __future__ import annotations

from src.schema import (
    AdditionalMount,
    AllowedRoot,
    Channel,
    ContainerConfig,
    MountAllowlist,
    NewMessage,
    RegisteredGroup,
    ScheduledTask,
    TaskRunLog,
)


def test_registered_group_defaults():
    g = RegisteredGroup(name="test", folder="test", trigger="@Bot", added_at="2026-01-01")
    assert g.container_config is None
    assert g.requires_trigger is None
    assert g.is_main is None


def test_new_message_defaults():
    m = NewMessage(
        id="1", chat_jid="jid", sender="s", sender_name="S",
        content="hi", timestamp="2026-01-01"
    )
    assert m.is_from_me is False
    assert m.is_bot_message is False


def test_scheduled_task_fields():
    t = ScheduledTask(
        id="t1", group_folder="g", chat_jid="jid",
        prompt="do thing", schedule_type="interval",
        schedule_value="60", context_mode="group",
        next_run=None, last_run=None, last_result=None,
        status="active", created_at="2026-01-01",
    )
    assert t.status == "active"
    assert t.schedule_type == "interval"


def test_mount_allowlist_defaults():
    ml = MountAllowlist()
    assert ml.allowed_roots == []
    assert ml.blocked_patterns == []
    assert ml.non_main_read_only is True


def test_additional_mount_readonly_default():
    m = AdditionalMount(host_path="/tmp/x")
    assert m.readonly is True
    assert m.container_path is None


def test_container_config_optional():
    cc = ContainerConfig()
    assert cc.additional_mounts == []
    assert cc.timeout is None


def test_channel_protocol_structural():
    """Channel is a runtime_checkable Protocol — a duck-typed object should satisfy it."""

    class FakeChannel:
        name = "fake"
        async def connect(self): ...
        async def send_message(self, jid, text): ...
        def is_connected(self): return True
        def owns_jid(self, jid): return False
        async def disconnect(self): ...
        async def set_typing(self, jid, is_typing): ...
        async def sync_groups(self, force): ...

    assert isinstance(FakeChannel(), Channel)


def test_backward_compat_shim():
    """src/types.py shim must still export everything."""
    from src.types import RegisteredGroup as RG, NewMessage as NM, Channel as Ch
    assert RG is RegisteredGroup
    assert NM is NewMessage
    assert Ch is Channel
