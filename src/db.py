"""SQLite database operations for J Claw."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .config import ASSISTANT_NAME, DATA_DIR, STORE_DIR
from .group_folder import is_valid_group_folder
from .schema import NewMessage, RegisteredGroup, ScheduledTask, TaskRunLog

logger = logging.getLogger(__name__)

_db: Optional[sqlite3.Connection] = None


def _get_db() -> sqlite3.Connection:
    assert _db is not None, "Database not initialized — call init_database() first"
    return _db


_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    jid TEXT PRIMARY KEY,
    name TEXT,
    last_message_time TEXT,
    channel TEXT,
    is_group INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT,
    chat_jid TEXT,
    sender TEXT,
    sender_name TEXT,
    content TEXT,
    timestamp TEXT,
    is_from_me INTEGER,
    is_bot_message INTEGER DEFAULT 0,
    PRIMARY KEY (id, chat_jid),
    FOREIGN KEY (chat_jid) REFERENCES chats(jid)
);
CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp);
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    group_folder TEXT NOT NULL,
    chat_jid TEXT NOT NULL,
    prompt TEXT NOT NULL,
    schedule_type TEXT NOT NULL,
    schedule_value TEXT NOT NULL,
    next_run TEXT,
    last_run TEXT,
    last_result TEXT,
    status TEXT DEFAULT 'active',
    context_mode TEXT DEFAULT 'isolated',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_next_run ON scheduled_tasks(next_run);
CREATE INDEX IF NOT EXISTS idx_status ON scheduled_tasks(status);
CREATE TABLE IF NOT EXISTS task_run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_task_run_logs ON task_run_logs(task_id, run_at);
CREATE TABLE IF NOT EXISTS router_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    group_folder TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS registered_groups (
    jid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    folder TEXT NOT NULL UNIQUE,
    trigger_pattern TEXT NOT NULL,
    added_at TEXT NOT NULL,
    container_config TEXT,
    requires_trigger INTEGER DEFAULT 1,
    is_main INTEGER DEFAULT 0
);
"""


def _run_migrations(db: sqlite3.Connection) -> None:
    for stmt, fallback in [
        (f"ALTER TABLE scheduled_tasks ADD COLUMN context_mode TEXT DEFAULT 'isolated'", None),
        (f"ALTER TABLE messages ADD COLUMN is_bot_message INTEGER DEFAULT 0", None),
        (f"ALTER TABLE registered_groups ADD COLUMN is_main INTEGER DEFAULT 0", None),
        (f"ALTER TABLE chats ADD COLUMN channel TEXT", None),
        (f"ALTER TABLE chats ADD COLUMN is_group INTEGER DEFAULT 0", None),
    ]:
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists


def init_database() -> None:
    global _db
    db_path = STORE_DIR / "messages.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = sqlite3.connect(str(db_path), check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _db.executescript(_SCHEMA)
    _run_migrations(_db)
    _db.commit()

    # Backfill migrations
    try:
        _db.execute(
            "UPDATE registered_groups SET is_main = 1 WHERE folder = 'main'"
        )
        _db.execute(
            "UPDATE messages SET is_bot_message = 1 WHERE content LIKE ?",
            (f"{ASSISTANT_NAME}:%",),
        )
        _db.execute(
            "UPDATE chats SET channel = 'whatsapp', is_group = 1 WHERE jid LIKE '%@g.us'"
        )
        _db.execute(
            "UPDATE chats SET channel = 'telegram', is_group = 1 WHERE jid LIKE 'tg:%'"
        )
        _db.execute(
            "UPDATE chats SET channel = 'discord', is_group = 1 WHERE jid LIKE 'dc:%'"
        )
        _db.commit()
    except Exception:
        pass

    _migrate_json_state()


def _init_test_database() -> None:
    global _db
    _db = sqlite3.connect(":memory:", check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _db.executescript(_SCHEMA)
    _db.commit()


# ---- Chat metadata ----

def store_chat_metadata(
    chat_jid: str,
    timestamp: str,
    name: Optional[str] = None,
    channel: Optional[str] = None,
    is_group: Optional[bool] = None,
) -> None:
    db = _get_db()
    group_int = None if is_group is None else (1 if is_group else 0)
    if name:
        db.execute("""
            INSERT INTO chats (jid, name, last_message_time, channel, is_group) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET
                name = excluded.name,
                last_message_time = MAX(last_message_time, excluded.last_message_time),
                channel = COALESCE(excluded.channel, channel),
                is_group = COALESCE(excluded.is_group, is_group)
        """, (chat_jid, name, timestamp, channel, group_int))
    else:
        db.execute("""
            INSERT INTO chats (jid, name, last_message_time, channel, is_group) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET
                last_message_time = MAX(last_message_time, excluded.last_message_time),
                channel = COALESCE(excluded.channel, channel),
                is_group = COALESCE(excluded.is_group, is_group)
        """, (chat_jid, chat_jid, timestamp, channel, group_int))
    db.commit()


def update_chat_name(chat_jid: str, name: str) -> None:
    from datetime import datetime, timezone
    db = _get_db()
    db.execute("""
        INSERT INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)
        ON CONFLICT(jid) DO UPDATE SET name = excluded.name
    """, (chat_jid, name, datetime.now(timezone.utc).isoformat()))
    db.commit()


def get_all_chats() -> list[dict]:
    rows = _get_db().execute(
        "SELECT jid, name, last_message_time, channel, is_group FROM chats ORDER BY last_message_time DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_last_group_sync() -> Optional[str]:
    row = _get_db().execute(
        "SELECT last_message_time FROM chats WHERE jid = '__group_sync__'"
    ).fetchone()
    return row["last_message_time"] if row else None


def set_last_group_sync() -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    _get_db().execute(
        "INSERT OR REPLACE INTO chats (jid, name, last_message_time) VALUES ('__group_sync__', '__group_sync__', ?)",
        (now,)
    )
    _get_db().commit()


# ---- Messages ----

def store_message(msg: NewMessage) -> None:
    db = _get_db()
    db.execute(
        "INSERT OR REPLACE INTO messages (id, chat_jid, sender, sender_name, content, timestamp, is_from_me, is_bot_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (msg.id, msg.chat_jid, msg.sender, msg.sender_name, msg.content,
         msg.timestamp, 1 if msg.is_from_me else 0, 1 if msg.is_bot_message else 0)
    )
    db.commit()


def get_new_messages(
    jids: list[str],
    last_timestamp: str,
    bot_prefix: str,
    limit: int = 200,
) -> tuple[list[NewMessage], str]:
    if not jids:
        return [], last_timestamp
    db = _get_db()
    placeholders = ",".join("?" * len(jids))
    sql = f"""
        SELECT * FROM (
            SELECT id, chat_jid, sender, sender_name, content, timestamp, is_from_me
            FROM messages
            WHERE timestamp > ? AND chat_jid IN ({placeholders})
              AND is_bot_message = 0 AND content NOT LIKE ?
              AND content != '' AND content IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        ) ORDER BY timestamp
    """
    rows = db.execute(sql, (last_timestamp, *jids, f"{bot_prefix}:%", limit)).fetchall()
    msgs = [_row_to_message(r) for r in rows]
    new_ts = last_timestamp
    for m in msgs:
        if m.timestamp > new_ts:
            new_ts = m.timestamp
    return msgs, new_ts


def get_messages_since(
    chat_jid: str,
    since_timestamp: str,
    bot_prefix: str,
    limit: int = 200,
) -> list[NewMessage]:
    sql = """
        SELECT * FROM (
            SELECT id, chat_jid, sender, sender_name, content, timestamp, is_from_me
            FROM messages
            WHERE chat_jid = ? AND timestamp > ?
              AND is_bot_message = 0 AND content NOT LIKE ?
              AND content != '' AND content IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        ) ORDER BY timestamp
    """
    rows = _get_db().execute(sql, (chat_jid, since_timestamp, f"{bot_prefix}:%", limit)).fetchall()
    return [_row_to_message(r) for r in rows]


def _row_to_message(row: sqlite3.Row) -> NewMessage:
    d = dict(row)
    return NewMessage(
        id=d["id"],
        chat_jid=d["chat_jid"],
        sender=d["sender"],
        sender_name=d["sender_name"],
        content=d["content"],
        timestamp=d["timestamp"],
        is_from_me=bool(d.get("is_from_me", 0)),
        is_bot_message=bool(d.get("is_bot_message", 0)),
    )


# ---- Scheduled tasks ----

def create_task(task: ScheduledTask) -> None:
    db = _get_db()
    db.execute("""
        INSERT INTO scheduled_tasks (id, group_folder, chat_jid, prompt, schedule_type, schedule_value, context_mode, next_run, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (task.id, task.group_folder, task.chat_jid, task.prompt,
          task.schedule_type, task.schedule_value, task.context_mode or "isolated",
          task.next_run, task.status, task.created_at))
    db.commit()


def get_task_by_id(task_id: str) -> Optional[ScheduledTask]:
    row = _get_db().execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def get_tasks_for_group(group_folder: str) -> list[ScheduledTask]:
    rows = _get_db().execute(
        "SELECT * FROM scheduled_tasks WHERE group_folder = ? ORDER BY created_at DESC",
        (group_folder,)
    ).fetchall()
    return [_row_to_task(r) for r in rows]


def get_all_tasks() -> list[ScheduledTask]:
    rows = _get_db().execute("SELECT * FROM scheduled_tasks ORDER BY created_at DESC").fetchall()
    return [_row_to_task(r) for r in rows]


def update_task(task_id: str, **updates) -> None:
    if not updates:
        return
    fields = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]
    _get_db().execute(f"UPDATE scheduled_tasks SET {fields} WHERE id = ?", values)
    _get_db().commit()


def delete_task(task_id: str) -> None:
    db = _get_db()
    db.execute("DELETE FROM task_run_logs WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
    db.commit()


def get_due_tasks() -> list[ScheduledTask]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    rows = _get_db().execute("""
        SELECT * FROM scheduled_tasks
        WHERE status = 'active' AND next_run IS NOT NULL AND next_run <= ?
        ORDER BY next_run
    """, (now,)).fetchall()
    return [_row_to_task(r) for r in rows]


def update_task_after_run(task_id: str, next_run: Optional[str], last_result: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    new_status = "completed" if next_run is None else None
    if new_status:
        _get_db().execute(
            "UPDATE scheduled_tasks SET next_run = ?, last_run = ?, last_result = ?, status = ? WHERE id = ?",
            (next_run, now, last_result, new_status, task_id)
        )
    else:
        _get_db().execute(
            "UPDATE scheduled_tasks SET next_run = ?, last_run = ?, last_result = ? WHERE id = ?",
            (next_run, now, last_result, task_id)
        )
    _get_db().commit()


def log_task_run(log: TaskRunLog) -> None:
    _get_db().execute(
        "INSERT INTO task_run_logs (task_id, run_at, duration_ms, status, result, error) VALUES (?, ?, ?, ?, ?, ?)",
        (log.task_id, log.run_at, log.duration_ms, log.status, log.result, log.error)
    )
    _get_db().commit()


def _row_to_task(row: sqlite3.Row) -> ScheduledTask:
    d = dict(row)
    return ScheduledTask(
        id=d["id"],
        group_folder=d["group_folder"],
        chat_jid=d["chat_jid"],
        prompt=d["prompt"],
        schedule_type=d["schedule_type"],
        schedule_value=d["schedule_value"],
        context_mode=d.get("context_mode", "isolated"),
        next_run=d.get("next_run"),
        last_run=d.get("last_run"),
        last_result=d.get("last_result"),
        status=d["status"],
        created_at=d["created_at"],
    )


# ---- Router state ----

def get_router_state(key: str) -> Optional[str]:
    row = _get_db().execute("SELECT value FROM router_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_router_state(key: str, value: str) -> None:
    _get_db().execute(
        "INSERT OR REPLACE INTO router_state (key, value) VALUES (?, ?)", (key, value)
    )
    _get_db().commit()


# ---- Sessions ----

def get_session(group_folder: str) -> Optional[str]:
    row = _get_db().execute(
        "SELECT session_id FROM sessions WHERE group_folder = ?", (group_folder,)
    ).fetchone()
    return row["session_id"] if row else None


def set_session(group_folder: str, session_id: str) -> None:
    _get_db().execute(
        "INSERT OR REPLACE INTO sessions (group_folder, session_id) VALUES (?, ?)",
        (group_folder, session_id)
    )
    _get_db().commit()


def get_all_sessions() -> dict[str, str]:
    rows = _get_db().execute("SELECT group_folder, session_id FROM sessions").fetchall()
    return {r["group_folder"]: r["session_id"] for r in rows}


# ---- Registered groups ----

def _row_to_group(row: sqlite3.Row) -> tuple[str, RegisteredGroup]:
    d = dict(row)
    from .schema import ContainerConfig, AdditionalMount
    cc = None
    if d.get("container_config"):
        try:
            raw = json.loads(d["container_config"])
            mounts = [
                AdditionalMount(
                    host_path=m.get("hostPath", ""),
                    container_path=m.get("containerPath"),
                    readonly=m.get("readonly", True),
                )
                for m in raw.get("additionalMounts", [])
            ]
            cc = ContainerConfig(
                additional_mounts=mounts,
                timeout=raw.get("timeout"),
            )
        except Exception:
            pass

    rt_raw = d.get("requires_trigger")
    rt = None if rt_raw is None else bool(rt_raw)
    is_main = bool(d.get("is_main", 0))

    group = RegisteredGroup(
        name=d["name"],
        folder=d["folder"],
        trigger=d["trigger_pattern"],
        added_at=d["added_at"],
        container_config=cc,
        requires_trigger=rt,
        is_main=is_main if is_main else None,
    )
    return d["jid"], group


def get_registered_group(jid: str) -> Optional[tuple[str, RegisteredGroup]]:
    row = _get_db().execute("SELECT * FROM registered_groups WHERE jid = ?", (jid,)).fetchone()
    if not row:
        return None
    j, g = _row_to_group(row)
    if not is_valid_group_folder(g.folder):
        logger.warning(f"Skipping registered group with invalid folder: {g.folder}")
        return None
    return j, g


def set_registered_group(jid: str, group: RegisteredGroup) -> None:
    if not is_valid_group_folder(group.folder):
        raise ValueError(f'Invalid group folder "{group.folder}" for JID {jid}')

    cc_json = None
    if group.container_config:
        mounts_raw = [
            {"hostPath": m.host_path, "containerPath": m.container_path, "readonly": m.readonly}
            for m in group.container_config.additional_mounts
        ]
        cc_json = json.dumps({"additionalMounts": mounts_raw, "timeout": group.container_config.timeout})

    rt = 1 if group.requires_trigger is None else (1 if group.requires_trigger else 0)

    _get_db().execute("""
        INSERT OR REPLACE INTO registered_groups (jid, name, folder, trigger_pattern, added_at, container_config, requires_trigger, is_main)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (jid, group.name, group.folder, group.trigger, group.added_at, cc_json, rt, 1 if group.is_main else 0))
    _get_db().commit()


def get_all_registered_groups() -> dict[str, RegisteredGroup]:
    rows = _get_db().execute("SELECT * FROM registered_groups").fetchall()
    result: dict[str, RegisteredGroup] = {}
    for row in rows:
        try:
            jid, group = _row_to_group(row)
        except Exception as e:
            logger.warning(f"Skipping malformed registered group row: {e}")
            continue
        if not is_valid_group_folder(group.folder):
            logger.warning(f"Skipping registered group with invalid folder: {group.folder}")
            continue
        result[jid] = group
    return result


# ---- JSON migration ----

def _migrate_json_state() -> None:
    def _migrate(filename: str):
        p = DATA_DIR / filename
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
            p.rename(str(p) + ".migrated")
            return data
        except Exception:
            return None

    router_state = _migrate("router_state.json")
    if router_state:
        if router_state.get("last_timestamp"):
            set_router_state("last_timestamp", router_state["last_timestamp"])
        if router_state.get("last_agent_timestamp"):
            set_router_state("last_agent_timestamp", json.dumps(router_state["last_agent_timestamp"]))

    sessions = _migrate("sessions.json")
    if sessions:
        for folder, session_id in sessions.items():
            set_session(folder, session_id)

    groups = _migrate("registered_groups.json")
    if groups:
        for jid, raw in groups.items():
            try:
                g = RegisteredGroup(
                    name=raw.get("name", jid),
                    folder=raw.get("folder", ""),
                    trigger=raw.get("trigger", ""),
                    added_at=raw.get("added_at", ""),
                )
                set_registered_group(jid, g)
            except Exception as e:
                logger.warning(f"Skipping migrated group {jid}: {e}")
