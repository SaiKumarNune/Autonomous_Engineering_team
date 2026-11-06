"""Persist workflow runs with the existing SQLite schema.

File contents stored here are redacted copies. The workspace keeps the generated
text that passed validation.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from database.setup_db import create_database
from tools.workspace import WorkspaceSecurityError, read_text

REPO_ROOT = Path(__file__).resolve().parents[1]
_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|authorization)\b(\s*[:=]\s*)(\S+)"
)
_MAX_TEXT = 4000

AGENT_STEPS = (
    (1, "Product Manager", "Produce requirements", "requirements"),
    (2, "Architect", "Produce architecture", "architecture"),
    (3, "Backend Engineer", "Generate backend artifacts", "backend_code"),
    (4, "Frontend Engineer", "Generate frontend artifacts", "frontend_code"),
    (5, "QA Engineer", "Generate pytest artifacts", "qa_tests"),
    (6, "DevOps Engineer", "Generate deployment artifacts", "devops_files"),
    (7, "Reviewer", "Review the generated project", "review"),
)


def default_db_path() -> str:
    configured = os.getenv("DATABASE_PATH", "").strip()
    if not configured:
        return str(REPO_ROOT / "database" / "software_team.db")
    path = Path(configured)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return str(path)


def redact(value: object) -> str:
    text = "" if value is None else str(value)
    return _SECRET.sub(r"\1\2[REDACTED]", text)


def persist_workflow_state(state: dict, db_path: str | os.PathLike[str] | None = None) -> int:
    """Insert the run, agent rows, files, and validation results in one transaction."""
    path = os.fspath(db_path) if db_path is not None else default_db_path()
    create_database(path)
    _ensure_run_columns(path)

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        run_id = redact(state.get("run_id", ""))[:64]
        status = redact(state.get("status", "failed"))[:32] or "failed"
        request = redact(state.get("user_request", ""))[:_MAX_TEXT]
        cursor = connection.execute(
            """
            INSERT INTO projects (project_name, run_id, user_request, status)
            VALUES (?, ?, ?, ?)
            """,
            (run_id or "unknown-run", run_id, request, status),
        )
        project_id = int(cursor.lastrowid)
        errors = _error_map(state.get("errors") or [])
        current_node = _current_node(state, errors)
        connection.execute(
            """
            INSERT INTO workflow_runs (
                project_id, workflow_name, run_id, current_node, status, state_json, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                project_id,
                "software_engineering_team_graph",
                run_id,
                current_node,
                status,
                _state_json(state),
            ),
        )
        _insert_agent_tasks(connection, project_id, state, errors)
        _insert_files(connection, project_id, state)
        _insert_validation(connection, project_id, state.get("validation_results") or [])
        _insert_test_results(connection, project_id, state.get("test_results") or [])
        _insert_repair_attempts(connection, state)
        if errors:
            connection.execute(
                """
                INSERT INTO agent_logs (project_id, agent_name, event_type, error_message)
                VALUES (?, ?, ?, ?)
                """,
                (
                    project_id,
                    "workflow",
                    "workflow_errors",
                    redact(json.dumps(errors))[:_MAX_TEXT],
                ),
            )
    return project_id


def _ensure_run_columns(db_path: str) -> None:
    with sqlite3.connect(db_path) as connection:
        _add_column(connection, "projects", "run_id", "TEXT")
        _add_column(connection, "workflow_runs", "run_id", "TEXT")


def _add_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _error_map(errors: object) -> dict[str, str]:
    mapped: dict[str, str] = {}
    if not isinstance(errors, list):
        return mapped
    for item in errors:
        if isinstance(item, dict):
            agent = str(item.get("agent") or "workflow")
            message = redact(item.get("message", ""))
        else:
            agent = "workflow"
            message = redact(item)
        mapped[agent] = message
    return mapped


def _current_node(state: dict, errors: dict[str, str]) -> str:
    for _order, name, _task, key in reversed(AGENT_STEPS):
        if state.get(key):
            return name
    if errors:
        return next(iter(errors))
    return "product_manager"


def _state_json(state: dict) -> str:
    payload = {
        "run_id": state.get("run_id", ""),
        "status": state.get("status", ""),
        "lifecycle_status": state.get("lifecycle_status", ""),
        "artifact_hash": state.get("artifact_hash", ""),
        "tests_passed": bool(state.get("tests_passed")),
        "repair_attempts": state.get("repair_attempts", 0),
        "errors": state.get("errors", []),
        "artifact_manifest": state.get("artifact_manifest", []),
        "validation_results": state.get("validation_results", []),
        "test_results": [
            {
                "kind": item.get("kind"),
                "exit_code": item.get("exit_code"),
                "sandbox_enforced": item.get("sandbox_enforced"),
                "message": item.get("message"),
                "failure_signature": item.get("failure_signature"),
            }
            for item in state.get("test_results") or []
            if isinstance(item, dict)
        ],
    }
    return redact(json.dumps(payload))[:20000]


def _insert_agent_tasks(connection, project_id: int, state: dict, errors: dict[str, str]) -> None:
    failed = False
    for order, name, task, key in AGENT_STEPS:
        output = redact(state.get(key) or "")[:_MAX_TEXT]
        error = errors.get(name, "")
        if output and not error:
            task_status = "completed"
        elif error:
            task_status = "failed"
            failed = True
        elif failed or errors.get("workflow") or errors.get("workspace") or errors.get("persistence"):
            task_status = "not_run"
        else:
            task_status = "not_run"
        connection.execute(
            """
            INSERT INTO agent_tasks (
                project_id, agent_name, task_name, task_order, output, status, error_message, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (project_id, name, task, order, output, task_status, error),
        )


def _insert_files(connection, project_id: int, state: dict) -> None:
    workspace = state.get("workspace_path") or ""
    for item in state.get("artifact_manifest") or []:
        relative = str(item.get("relative_path") or "")
        content = ""
        applied = 0
        if workspace and relative:
            try:
                content = redact(read_text(Path(workspace), relative))
                applied = 1
            except (OSError, WorkspaceSecurityError):
                content = "[file unavailable]"
                applied = 0
        connection.execute(
            """
            INSERT INTO generated_files (
                project_id, agent_name, file_path, file_type, content, is_applied
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                str(item.get("source_agent") or "unknown"),
                relative,
                str(item.get("language") or "text"),
                content,
                applied,
            ),
        )


def _insert_validation(connection, project_id: int, results: list) -> None:
    for result in results:
        if not isinstance(result, dict):
            continue
        valid = bool(result.get("valid"))
        notes = redact("; ".join(str(issue) for issue in result.get("issues") or []))
        limitation = redact(result.get("limitation", ""))
        if limitation:
            notes = f"{notes} {limitation}".strip()
        connection.execute(
            """
            INSERT INTO evaluation_results (
                project_id, evaluation_name, metric_name, score, max_score, notes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                str(result.get("agent") or "workflow"),
                str(result.get("kind") or "validation"),
                1.0 if valid else 0.0,
                1.0,
                notes[:_MAX_TEXT],
            ),
        )


def record_approval(db_path: str | os.PathLike[str] | None, run_id: str, artifact_hash: str, decision: str) -> None:
    """Store one human decision for a run id and artifact hash."""
    if decision not in {"approved", "rejected"}:
        raise ValueError("Approval decision must be approved or rejected.")
    if not run_id or not artifact_hash:
        raise ValueError("Approval requires a run id and an artifact hash.")
    path = os.fspath(db_path) if db_path is not None else default_db_path()
    create_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO approvals (run_id, artifact_hash, decision)
            VALUES (?, ?, ?)
            """,
            (redact(run_id)[:64], redact(artifact_hash)[:64], decision),
        )


def latest_approval(db_path: str | os.PathLike[str] | None, run_id: str, artifact_hash: str) -> str | None:
    path = os.fspath(db_path) if db_path is not None else default_db_path()
    if not Path(path).is_file():
        return None
    with sqlite3.connect(path) as connection:
        try:
            row = connection.execute(
                """
                SELECT decision FROM approvals
                WHERE run_id = ? AND artifact_hash = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id, artifact_hash),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    if row is None:
        return None
    return str(row[0])


def _insert_test_results(connection, project_id: int, results: list) -> None:
    for item in results:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "sandbox_error")
        output = redact(
            f"duration_ms={item.get('duration_ms', 0)}\n"
            f"{item.get('message', '')}\n"
            f"{item.get('stdout', '')}\n"
            f"{item.get('stderr', '')}"
        )[:_MAX_TEXT]
        connection.execute(
            """
            INSERT INTO test_results (
                project_id, test_name, test_type, status, command, output, passed_count, failed_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                "pytest",
                kind,
                kind,
                redact(" ".join(str(part) for part in item.get("command") or []))[:500],
                output,
                1 if kind == "passed" else 0,
                1 if kind == "failed" else 0,
            ),
        )


def _insert_repair_attempts(connection, state: dict) -> None:
    run_id = redact(state.get("run_id", ""))[:64]
    for item in state.get("repair_history") or []:
        if not isinstance(item, dict):
            continue
        connection.execute(
            """
            INSERT INTO repair_attempts (
                run_id, attempt, status, failure_signature, diff_preview,
                artifact_hash_before, artifact_hash_after
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                int(item.get("attempt") or 0),
                redact(item.get("status", ""))[:32],
                redact(item.get("failure_signature", ""))[:64],
                redact(item.get("diff", ""))[:_MAX_TEXT],
                redact(item.get("artifact_hash_before", ""))[:64],
                redact(item.get("artifact_hash_after", ""))[:64],
            ),
        )
