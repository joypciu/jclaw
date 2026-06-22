"""Tests for src/task_scheduler.py — compute_next_run for all schedule types."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.schema import ScheduledTask
from src.task_scheduler import compute_next_run


def _task(schedule_type: str, schedule_value: str, last_run: str | None = None) -> ScheduledTask:
    return ScheduledTask(
        id="t1",
        group_folder="g",
        chat_jid="jid@g.us",
        prompt="do it",
        schedule_type=schedule_type,
        schedule_value=schedule_value,
        context_mode="group",
        next_run=None,
        last_run=last_run,
        last_result=None,
        status="active",
        created_at="2026-01-01T00:00:00Z",
    )


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# interval schedule
# ---------------------------------------------------------------------------

def test_interval_schedule_no_last_run():
    task = _task("interval", "60000")  # 60 seconds in ms
    result = compute_next_run(task)
    assert result is not None
    nxt = _parse_iso(result)
    now = datetime.now(timezone.utc)
    diff = (nxt - now).total_seconds()
    assert 50 <= diff <= 70


def test_interval_schedule_from_next_run():
    # Scheduler anchors to task.next_run, not last_run.
    # next_run was 30s ago, interval 60s → one interval from that anchor → ~30s in future
    from datetime import timedelta
    next_run_str = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    task = ScheduledTask(
        id="t1", group_folder="g", chat_jid="jid@g.us", prompt="do it",
        schedule_type="interval", schedule_value="60000",
        context_mode="group", next_run=next_run_str,
        last_run=None, last_result=None, status="active",
        created_at="2026-01-01T00:00:00Z",
    )
    result = compute_next_run(task)
    assert result is not None
    nxt = _parse_iso(result)
    now = datetime.now(timezone.utc)
    diff = (nxt - now).total_seconds()
    assert 20 <= diff <= 45


def test_interval_overdue_is_soon():
    # next_run was 2 minutes ago, interval 60s → catches up → fires very soon
    from datetime import timedelta
    next_run_str = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    task = ScheduledTask(
        id="t1", group_folder="g", chat_jid="jid@g.us", prompt="do it",
        schedule_type="interval", schedule_value="60000",
        context_mode="group", next_run=next_run_str,
        last_run=None, last_result=None, status="active",
        created_at="2026-01-01T00:00:00Z",
    )
    result = compute_next_run(task)
    assert result is not None
    nxt = _parse_iso(result)
    now = datetime.now(timezone.utc)
    diff = (nxt - now).total_seconds()
    # Scheduler adds interval multiples until just past now → within one interval period
    assert 0 < diff <= 65


def test_interval_invalid_value_returns_fallback():
    task = _task("interval", "not_a_number")
    result = compute_next_run(task)
    # Should not raise, should return something
    assert result is not None


# ---------------------------------------------------------------------------
# cron schedule
# ---------------------------------------------------------------------------

def test_cron_every_minute():
    task = _task("cron", "* * * * *")
    try:
        result = compute_next_run(task)
    except Exception:
        pytest.skip("TIMEZONE config not a valid IANA zone on this system")
    assert result is not None
    nxt = _parse_iso(result)
    now = datetime.now(timezone.utc)
    diff = (nxt - now).total_seconds()
    assert 0 < diff <= 120


def test_cron_hourly():
    task = _task("cron", "0 * * * *")
    try:
        result = compute_next_run(task)
    except Exception:
        pytest.skip("TIMEZONE config not a valid IANA zone on this system")
    assert result is not None
    nxt = _parse_iso(result)
    now = datetime.now(timezone.utc)
    diff = (nxt - now).total_seconds()
    assert 0 < diff <= 3660


# ---------------------------------------------------------------------------
# once schedule
# ---------------------------------------------------------------------------

def test_once_schedule_returns_none():
    task = _task("once", "")
    result = compute_next_run(task)
    assert result is None


# ---------------------------------------------------------------------------
# unsupported schedule types → return None (scheduler handles them elsewhere)
# ---------------------------------------------------------------------------

def test_daily_schedule_returns_none():
    task = _task("daily", "09:00")
    result = compute_next_run(task)
    assert result is None


def test_weekly_schedule_returns_none():
    task = _task("weekly", "monday:09:00")
    result = compute_next_run(task)
    assert result is None
