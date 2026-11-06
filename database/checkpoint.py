"""SQLite-backed LangGraph checkpointer.

LangGraph's separate SQLite saver package is not installed. This saver keeps the
in-memory checkpoint format and writes it to the existing workflow database so a
new process can resume the same run. The payload is JSON, not a pickle of model
output.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

_TABLE = """
CREATE TABLE IF NOT EXISTS graph_checkpoints (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


class CheckpointIntegrityError(RuntimeError):
    """The stored checkpoint cannot be loaded. The run must not continue from it."""


class SqliteCheckpointSaver(InMemorySaver):
    def __init__(self, db_path: str) -> None:
        super().__init__()
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._restore()

    def put(self, config, checkpoint, metadata, new_versions):
        with self._lock:
            saved = super().put(config, checkpoint, metadata, new_versions)
            self._persist()
            return saved

    def put_writes(self, config, writes, task_id, task_path=""):
        with self._lock:
            super().put_writes(config, writes, task_id, task_path)
            self._persist()

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super().delete_thread(thread_id)
            self._persist()

    def _export(self) -> dict[str, Any]:
        return {
            "storage": {
                thread: {namespace: dict(items) for namespace, items in namespaces.items()}
                for thread, namespaces in self.storage.items()
            },
            "writes": {key: dict(value) for key, value in self.writes.items()},
            "blobs": dict(self.blobs),
        }

    def _persist(self) -> None:
        payload = json.dumps(freeze(self._export()))
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(_TABLE)
            connection.execute(
                """
                INSERT INTO graph_checkpoints (id, payload, updated_at)
                VALUES (1, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = datetime('now')
                """,
                (payload,),
            )

    def _restore(self) -> None:
        if not Path(self.db_path).is_file():
            return
        with sqlite3.connect(self.db_path) as connection:
            try:
                row = connection.execute(
                    "SELECT payload FROM graph_checkpoints WHERE id = 1"
                ).fetchone()
            except sqlite3.OperationalError:
                return
        if row is None:
            return
        try:
            data = thaw(json.loads(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CheckpointIntegrityError(f"Checkpoint payload is invalid: {exc}") from exc
        if not isinstance(data, dict) or "storage" not in data:
            raise CheckpointIntegrityError("Checkpoint payload is missing storage.")
        self.storage = defaultdict(lambda: defaultdict(dict))
        for thread_id, namespaces in data.get("storage", {}).items():
            for namespace, checkpoints in namespaces.items():
                self.storage[thread_id][namespace].update(checkpoints)
        self.writes = defaultdict(dict)
        for key, writes in data.get("writes", {}).items():
            self.writes[key].update(writes)
        self.blobs.clear()
        self.blobs.update(data.get("blobs", {}))


def freeze(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return {"__tuple__": [freeze(item) for item in value]}
    if isinstance(value, list):
        return [freeze(item) for item in value]
    if isinstance(value, dict):
        return {"__map__": [[freeze(key), freeze(item)] for key, item in value.items()]}
    raise TypeError(f"Unsupported checkpoint value: {type(value).__name__}")


def thaw(value: Any) -> Any:
    if isinstance(value, list):
        return [thaw(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"__bytes__"}:
            return base64.b64decode(value["__bytes__"])
        if set(value) == {"__tuple__"}:
            return tuple(thaw(item) for item in value["__tuple__"])
        if set(value) == {"__map__"}:
            return {thaw(key): thaw(item) for key, item in value["__map__"]}
    return value
