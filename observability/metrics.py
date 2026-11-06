"""Persisted per-run metrics. Missing token counts stay null."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from observability.context import current_db_path, current_run_id
from observability.logging import log_event

_TABLE = """
CREATE TABLE IF NOT EXISTS run_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    agent_name TEXT,
    duration_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    detail_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def record_metric(
    *,
    metric_name: str,
    agent_name: str | None = None,
    duration_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    detail: dict[str, Any] | None = None,
    run_id: str | None = None,
    db_path: str | None = None,
) -> None:
    selected_run = run_id or current_run_id()
    database = db_path or current_db_path()
    if not selected_run or not database:
        log_event(
            "metric_skipped",
            metric_name=metric_name,
            reason="no active run context",
        )
        return
    with sqlite3.connect(database) as connection:
        connection.execute(_TABLE)
        connection.execute(
            """
            INSERT INTO run_metrics (
                run_id, metric_name, agent_name, duration_ms,
                prompt_tokens, completion_tokens, total_tokens, detail_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                selected_run,
                metric_name,
                agent_name,
                duration_ms,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                json.dumps(detail or {}, default=str),
            ),
        )
    log_event(
        "metric",
        run_id=selected_run,
        metric_name=metric_name,
        agent_name=agent_name,
        duration_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def summarize_metrics(db_path: str, run_id: str) -> dict[str, Any]:
    """Aggregate stored metrics. Token totals are included only when rows reported them."""
    if not db_path:
        return _empty_summary(run_id)
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT metric_name, agent_name, duration_ms, prompt_tokens,
                       completion_tokens, total_tokens, detail_json
                FROM run_metrics
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return _empty_summary(run_id)

    stages = []
    invocations = []
    prompt_tokens = None
    completion_tokens = None
    total_tokens = None
    token_rows = 0
    sandbox_duration_ms = None
    for row in rows:
        detail = {}
        if row["detail_json"]:
            try:
                detail = json.loads(row["detail_json"])
            except json.JSONDecodeError:
                detail = {}
        item = {
            "metric_name": row["metric_name"],
            "agent_name": row["agent_name"],
            "duration_ms": row["duration_ms"],
            "detail": detail,
        }
        if row["metric_name"] == "model_invocation":
            invocations.append(
                {
                    **item,
                    "prompt_tokens": row["prompt_tokens"],
                    "completion_tokens": row["completion_tokens"],
                    "total_tokens": row["total_tokens"],
                }
            )
            if row["prompt_tokens"] is not None or row["completion_tokens"] is not None or row["total_tokens"] is not None:
                token_rows += 1
                prompt_tokens = _add(prompt_tokens, row["prompt_tokens"])
                completion_tokens = _add(completion_tokens, row["completion_tokens"])
                total_tokens = _add(total_tokens, row["total_tokens"])
        elif row["metric_name"] == "stage":
            stages.append(item)
        elif row["metric_name"] == "sandbox_execution" and row["duration_ms"] is not None:
            sandbox_duration_ms = int(row["duration_ms"])
    return {
        "run_id": run_id,
        "stages": stages,
        "model_invocations": invocations,
        "sandbox_duration_ms": sandbox_duration_ms,
        "token_usage": {
            "reported": token_rows > 0,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


def _add(current: int | None, value: int | None) -> int | None:
    if value is None:
        return current
    return (current or 0) + value


def _empty_summary(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "stages": [],
        "model_invocations": [],
        "sandbox_duration_ms": None,
        "token_usage": {
            "reported": False,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        },
    }
