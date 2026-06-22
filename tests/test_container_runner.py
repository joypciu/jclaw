"""Tests for src/container_runner.py — payload prep, env building, IPC helpers."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.schema import ContainerConfig, RegisteredGroup
from src.container_runner import (
    ContainerInput,
    ContainerOutput,
    _SIGNAL_EXIT_CODES,
    datetime_now_stamp,
    datetime_now_iso,
    write_tasks_snapshot,
    write_groups_snapshot,
    AvailableGroup,
)


def _group(folder: str = "testgroup", is_main: bool = False) -> RegisteredGroup:
    return RegisteredGroup(
        name="Test",
        folder=folder,
        trigger="@Bot",
        added_at="2026-01-01",
        is_main=is_main,
    )


def test_container_output_defaults():
    out = ContainerOutput(status="success", result="hello")
    assert out.new_session_id is None
    assert out.error is None


def test_container_input_fields():
    inp = ContainerInput(
        prompt="do it",
        session_id=None,
        group_folder="g",
        chat_jid="jid@s.whatsapp.net",
        is_main=False,
    )
    assert inp.is_scheduled_task is False
    assert inp.assistant_name is None


def test_signal_exit_codes_set():
    # These codes must be treated as clean exits after streaming output
    assert 130 in _SIGNAL_EXIT_CODES   # SIGINT shell convention
    assert 143 in _SIGNAL_EXIT_CODES   # SIGTERM shell convention
    assert -15 in _SIGNAL_EXIT_CODES   # SIGTERM direct
    assert -2 in _SIGNAL_EXIT_CODES    # SIGINT direct


def test_datetime_now_stamp_format():
    ts = datetime_now_stamp()
    assert len(ts) == 19
    assert "T" in ts
    assert "-" in ts


def test_datetime_now_iso_format():
    ts = datetime_now_iso()
    assert ts.endswith("Z")
    assert "T" in ts


def test_write_tasks_snapshot(tmp_path):
    with patch("src.container_runner.resolve_group_ipc_path", return_value=tmp_path):
        tasks = [{"id": "t1", "groupFolder": "g", "status": "active"}]
        write_tasks_snapshot("g", is_main=True, tasks=tasks)
        out = json.loads((tmp_path / "current_tasks.json").read_text())
        assert len(out) == 1
        assert out[0]["id"] == "t1"


def test_write_tasks_snapshot_filtered(tmp_path):
    """Non-main groups only see their own tasks."""
    with patch("src.container_runner.resolve_group_ipc_path", return_value=tmp_path):
        tasks = [
            {"id": "t1", "groupFolder": "groupA"},
            {"id": "t2", "groupFolder": "groupB"},
        ]
        write_tasks_snapshot("groupA", is_main=False, tasks=tasks)
        out = json.loads((tmp_path / "current_tasks.json").read_text())
        assert len(out) == 1
        assert out[0]["id"] == "t1"


def test_write_groups_snapshot_main_sees_all(tmp_path):
    with patch("src.container_runner.resolve_group_ipc_path", return_value=tmp_path):
        groups = [
            AvailableGroup(jid="a@g.us", name="A", last_activity="now", is_registered=True),
            AvailableGroup(jid="b@g.us", name="B", last_activity="now", is_registered=False),
        ]
        write_groups_snapshot("main", is_main=True, groups=groups, _registered_jids={"a@g.us"})
        out = json.loads((tmp_path / "available_groups.json").read_text())
        assert len(out["groups"]) == 2


def test_write_groups_snapshot_non_main_sees_none(tmp_path):
    with patch("src.container_runner.resolve_group_ipc_path", return_value=tmp_path):
        groups = [AvailableGroup(jid="a@g.us", name="A", last_activity="now", is_registered=True)]
        write_groups_snapshot("other", is_main=False, groups=groups, _registered_jids=set())
        out = json.loads((tmp_path / "available_groups.json").read_text())
        assert out["groups"] == []


def test_temp_file_ipc_payload_roundtrip():
    """Verify that the payload written to temp file can be read back by Node conventions."""
    payload = {
        "prompt": "hello world",
        "sessionId": None,
        "groupFolder": "mygroup",
        "chatJid": "123@g.us",
        "isMain": False,
        "isScheduledTask": False,
        "assistantName": "Bot",
    }
    fd, path = tempfile.mkstemp(suffix=".json", prefix="jclaw-test-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        read_back = json.loads(Path(path).read_text(encoding="utf-8"))
        assert read_back == payload
    finally:
        if os.path.exists(path):
            os.unlink(path)
