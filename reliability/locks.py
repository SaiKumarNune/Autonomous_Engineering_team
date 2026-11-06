"""One workflow execution at a time for a database, so checkpoints cannot clobber each other."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

LOCK_TTL = timedelta(hours=6)
_LOCK_NAME = "workflow"


class RunInProgressError(RuntimeError):
    """Another workflow invocation owns this database."""


def acquire_execution_lock(db_path: str, run_id: str) -> str:
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    with sqlite3.connect(db_path, timeout=5) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_locks (
                lock_name TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                acquired_at TEXT NOT NULL
            )
            """
        )
        row = connection.execute(
            "SELECT run_id, owner, acquired_at FROM run_locks WHERE lock_name = ?",
            (_LOCK_NAME,),
        ).fetchone()
        if row is not None and not _stale(row[1], row[2]):
            raise RunInProgressError(
                f"A workflow run is already in progress for this database (run {row[0]})."
            )
        connection.execute("DELETE FROM run_locks WHERE lock_name = ?", (_LOCK_NAME,))
        connection.execute(
            """
            INSERT INTO run_locks (lock_name, run_id, owner, acquired_at)
            VALUES (?, ?, ?, ?)
            """,
            (_LOCK_NAME, run_id, owner, _now()),
        )
        connection.commit()
    return owner


def release_execution_lock(db_path: str, owner: str) -> None:
    with sqlite3.connect(db_path, timeout=5) as connection:
        connection.execute(
            "DELETE FROM run_locks WHERE lock_name = ? AND owner = ?",
            (_LOCK_NAME, owner),
        )


@contextmanager
def execution_lock(db_path: str, run_id: str):
    owner = acquire_execution_lock(db_path, run_id)
    try:
        yield owner
    finally:
        release_execution_lock(db_path, owner)


def _stale(owner: str, acquired_at: str) -> bool:
    if not _owner_alive(owner):
        return True
    try:
        acquired = datetime.fromisoformat(acquired_at)
    except ValueError:
        return True
    if acquired.tzinfo is None:
        acquired = acquired.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - acquired > LOCK_TTL


def _owner_alive(owner: str) -> bool:
    pid_text = owner.split("-", 1)[0]
    if not pid_text.isdigit():
        return False
    pid = int(pid_text)
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
