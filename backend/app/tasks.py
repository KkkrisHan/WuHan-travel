from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from fastapi import HTTPException

from .config import settings
from .models import TaskCreated, TaskEvent
from .request_context import request_id

_LOG = logging.getLogger(__name__)


DB = Path(settings.task_database)
DB.parent.mkdir(parents=True, exist_ok=True)
RUNNING: dict[str, asyncio.Task] = {}
TRIP_LOCKS: dict[str, str] = {}


@contextmanager
def _connect():
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    with _connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, trip_id TEXT NOT NULL, kind TEXT NOT NULL, token_hash TEXT NOT NULL, base_revision INTEGER NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, lease_until REAL NOT NULL, trip_secret_hash TEXT NOT NULL DEFAULT '')")
        if "trip_secret_hash" not in [row[1] for row in db.execute("PRAGMA table_info(tasks)")]:
            db.execute("ALTER TABLE tasks ADD COLUMN trip_secret_hash TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE TABLE IF NOT EXISTS events (task_id TEXT NOT NULL, event_id INTEGER NOT NULL, event_json TEXT NOT NULL, PRIMARY KEY(task_id,event_id))")
        db.execute("CREATE INDEX IF NOT EXISTS tasks_trip ON tasks(trip_id)")
        # No task is resumed across server restarts.
        db.execute("UPDATE tasks SET status='cancelled' WHERE status IN ('pending','running')")
        db.execute("DELETE FROM events WHERE task_id IN (SELECT id FROM tasks WHERE created_at < strftime('%s','now') - 86400)")
        db.execute("DELETE FROM tasks WHERE created_at < strftime('%s','now') - 86400")


def _row(task_id: str):
    with _connect() as db:
        return db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()


def authorized(task_id: str, token: str):
    row = _row(task_id)
    if not row or not secrets.compare_digest(row["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
        raise HTTPException(403, "任务访问凭证无效")
    return row


async def emit(task_id: str, event_type: str, payload: dict):
    with _connect() as db:
        event_id = db.execute("SELECT COALESCE(MAX(event_id), 0) + 1 FROM events WHERE task_id=?", (task_id,)).fetchone()[0]
        event = TaskEvent(taskId=task_id, eventId=event_id, type=event_type, payload=payload)
        db.execute("INSERT INTO events VALUES (?,?,?)", (task_id, event_id, event.model_dump_json()))
        db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (datetime.now(timezone.utc).timestamp(), task_id))


def update_status(task_id: str, status: str, result=None, error=None):
    with _connect() as db:
        db.execute("UPDATE tasks SET status=?, result=?, error=?, updated_at=? WHERE id=?", (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, datetime.now(timezone.utc).timestamp(), task_id))


def events_after(task_id: str, last: int):
    with _connect() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT event_json FROM events WHERE task_id=? AND event_id>? ORDER BY event_id", (task_id, last))]


def task_info(task_id: str, token: str):
    row = authorized(task_id, token)
    return {"taskId": row["id"], "tripId": row["trip_id"], "baseRevision": row["base_revision"], "status": row["status"], "result": json.loads(row["result"]) if row["result"] else None, "error": row["error"]}


async def create_task(trip_id: str, trip_secret: str, revision: int, kind: str, worker: Callable[[Callable[[str, dict], Awaitable[None]]], Awaitable[object]]) -> TaskCreated:
    if len(RUNNING) >= 8:
        raise HTTPException(429, "服务繁忙，请稍后重试")
    if kind in ("planning", "optimization") and trip_id in TRIP_LOCKS:
        raise HTTPException(409, "此旅行正在规划，请先等待或取消当前任务")
    task_id, token = uuid4().hex, secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).timestamp()
    with _connect() as db:
        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (task_id, trip_id, kind, hashlib.sha256(token.encode()).hexdigest(), revision, "pending", None, None, now, now, now + 25, hashlib.sha256(trip_secret.encode()).hexdigest()))
    if kind in ("planning", "optimization"):
        TRIP_LOCKS[trip_id] = task_id

    async def run():
        started = time.monotonic()
        async def send(event_type: str, payload: dict):
            await emit(task_id, event_type, payload)
        try:
            _LOG.info("task_started request_id=%s task_id=%s kind=%s", request_id.get(), task_id, kind)
            update_status(task_id, "running")
            await send("task_started", {"kind": kind})
            result = await asyncio.wait_for(worker(send), timeout=120)
            update_status(task_id, "completed", result)
            await send("task_completed", {"result": result})
            _LOG.info("task_completed request_id=%s task_id=%s kind=%s elapsed_ms=%d", request_id.get(), task_id, kind, int((time.monotonic()-started)*1000))
        except asyncio.CancelledError:
            update_status(task_id, "cancelled")
            await send("task_cancelled", {})
            _LOG.warning("task_cancelled request_id=%s task_id=%s kind=%s elapsed_ms=%d", request_id.get(), task_id, kind, int((time.monotonic()-started)*1000))
        except Exception as exc:
            message = "AI 生成超时，请重试" if isinstance(exc, asyncio.TimeoutError) else str(exc)
            update_status(task_id, "failed", error=message)
            await send("task_failed", {"message": message})
            _LOG.error("task_failed request_id=%s task_id=%s kind=%s category=%s elapsed_ms=%d", request_id.get(), task_id, kind, type(exc).__name__, int((time.monotonic()-started)*1000))
        finally:
            RUNNING.pop(task_id, None)
            if TRIP_LOCKS.get(trip_id) == task_id:
                TRIP_LOCKS.pop(trip_id, None)

    RUNNING[task_id] = asyncio.create_task(run())
    return TaskCreated(taskId=task_id, accessToken=token, baseRevision=revision)


def heartbeat(task_id: str, token: str):
    row = authorized(task_id, token)
    if row["status"] in ("pending", "running"):
        with _connect() as db:
            db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (datetime.now(timezone.utc).timestamp() + 25, task_id))
    return {"status": row["status"]}


def cancel(task_id: str, token: str):
    row = authorized(task_id, token)
    running = RUNNING.get(task_id)
    if running and not running.done():
        running.cancel()
    elif row["status"] in ("pending", "running"):
        update_status(task_id, "cancelled")
    return {"status": "cancelled"}


async def delete_trip_tasks(trip_id: str, trip_secret: str):
    with _connect() as db:
        rows = list(db.execute("SELECT id,trip_secret_hash FROM tasks WHERE trip_id=?", (trip_id,)))
    if rows and any(not secrets.compare_digest(row["trip_secret_hash"], hashlib.sha256(trip_secret.encode()).hexdigest()) for row in rows):
        raise HTTPException(403, "旅行访问凭证无效")
    ids = [row["id"] for row in rows]
    running_tasks = []
    for task_id in ids:
        running = RUNNING.get(task_id)
        if running:
            running.cancel()
            running_tasks.append(running)
    if running_tasks:
        await asyncio.gather(*running_tasks, return_exceptions=True)
    with _connect() as db:
        db.executemany("DELETE FROM events WHERE task_id=?", [(id,) for id in ids])
        db.execute("DELETE FROM tasks WHERE trip_id=?", (trip_id,))
    TRIP_LOCKS.pop(trip_id, None)


async def lease_watchdog():
    while True:
        await asyncio.sleep(5)
        now = datetime.now(timezone.utc).timestamp()
        with _connect() as db:
            stale = [row[0] for row in db.execute("SELECT id FROM tasks WHERE status IN ('pending','running') AND lease_until<?", (now,))]
        for task_id in stale:
            running = RUNNING.get(task_id)
            if running:
                running.cancel()

