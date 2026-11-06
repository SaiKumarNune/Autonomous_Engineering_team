"""Small real-run benchmark. Unit tests must not call this against Ollama."""

from __future__ import annotations

import time
from typing import Callable

TASKS = (
    {
        "task_id": "task-api",
        "request": (
            "Build a simple task management REST API using FastAPI and SQLite, "
            "supporting create, list, update, and delete operations, with pytest tests."
        ),
    },
    {
        "task_id": "task-inventory",
        "request": (
            "Build a Python command-line inventory counter that stores items in a JSON file "
            "and has pytest tests for add and list. Use only the Python standard library and pytest."
        ),
    },
    {
        "task_id": "task-csv",
        "request": (
            "Build a Python function library that parses a simple CSV string into records, "
            "with pytest tests and no third-party packages."
        ),
    },
)


def summarize_benchmark(runs: list[dict]) -> dict:
    """Summarize runs. A sandbox pass is not evidence that the program is correct."""
    real = [item for item in runs if item.get("execution") == "ollama"]
    count = len(real)
    return {
        "execution": "ollama" if real else "none",
        "tasks": count,
        "generation_success_rate": _rate(real, lambda item: item.get("generation_succeeded") is True),
        "artifact_validation_success_rate": _rate(
            real, lambda item: item.get("artifact_validation_succeeded") is True
        ),
        "sandbox_test_pass_rate": _rate(real, lambda item: item.get("sandbox_passed") is True),
        "repair_success_rate": _repair_rate(real),
        "average_duration_seconds": _average(real),
        "approval_ready_projects": sum(1 for item in real if item.get("approval_ready") is True),
        "sandbox_pass_does_not_prove_correctness": True,
    }


def measure_run(task: dict, runner: Callable[[str, str], dict], run_id: str) -> dict:
    started = time.perf_counter()
    state = runner(task["request"], run_id)
    duration = time.perf_counter() - started
    validation = state.get("validation_results") or []
    blocking_failures = [
        item
        for item in validation
        if isinstance(item, dict) and item.get("blocking") and not item.get("valid")
    ]
    sandbox_passed = bool(state.get("tests_passed")) and state.get("status") in {
        "awaiting_approval",
        "approved",
    }
    repair_attempts = int(state.get("repair_attempts") or 0)
    agent_errors = [
        item
        for item in state.get("errors") or []
        if isinstance(item, dict)
        and item.get("agent") not in {"sandbox", "repair", "approval", "workflow"}
    ]
    generation_succeeded = not agent_errors and bool(state.get("artifact_manifest"))
    return {
        "execution": "ollama",
        "task_id": task["task_id"],
        "run_id": state.get("run_id", run_id),
        "status": state.get("status"),
        "lifecycle_status": state.get("lifecycle_status"),
        "generation_succeeded": generation_succeeded,
        "artifact_validation_succeeded": generation_succeeded and not blocking_failures,
        "sandbox_passed": sandbox_passed,
        "repair_attempted": repair_attempts > 0,
        "repair_succeeded": repair_attempts > 0 and sandbox_passed,
        "repair_attempts": repair_attempts,
        "approval_ready": state.get("status") == "awaiting_approval" and bool(state.get("artifact_hash")),
        "artifact_count": len(state.get("artifact_manifest") or []),
        "duration_seconds": round(duration, 3),
        "errors": state.get("errors") or [],
        "workspace_path": state.get("workspace_path"),
        "artifact_hash": state.get("artifact_hash"),
        "agents_completed": {
            "product_manager": bool(state.get("requirements")),
            "architect": bool(state.get("architecture")),
            "backend": bool(state.get("backend_code")),
            "frontend": bool(state.get("frontend_code")),
            "qa": bool(state.get("qa_tests")),
            "devops": bool(state.get("devops_files")),
            "reviewer": bool(state.get("review")),
        },
        "files": [
            item.get("relative_path")
            for item in state.get("artifact_manifest") or []
            if isinstance(item, dict)
        ],
        "repair_history": [
            {
                "attempt": item.get("attempt"),
                "status": item.get("status"),
                "summary": item.get("summary"),
            }
            for item in state.get("repair_history") or []
            if isinstance(item, dict)
        ],
        "dependency_inspection": state.get("dependency_inspection"),
        "consistency": state.get("consistency"),
        "run_metrics": state.get("run_metrics"),
        "test_results": [
            {
                "kind": item.get("kind"),
                "exit_code": item.get("exit_code"),
                "sandbox_enforced": item.get("sandbox_enforced"),
                "message": item.get("message"),
            }
            for item in state.get("test_results") or []
            if isinstance(item, dict)
        ],
        "token_usage": (state.get("run_metrics") or {}).get("token_usage"),
        "sandbox_pass_does_not_prove_correctness": True,
    }


def _rate(runs: list[dict], predicate) -> float | None:
    if not runs:
        return None
    return round(sum(1 for item in runs if predicate(item)) / len(runs), 3)


def _repair_rate(runs: list[dict]) -> float | None:
    attempted = [item for item in runs if item.get("repair_attempted")]
    if not attempted:
        return None
    return round(sum(1 for item in attempted if item.get("repair_succeeded")) / len(attempted), 3)


def _average(runs: list[dict]) -> float | None:
    if not runs:
        return None
    return round(sum(float(item.get("duration_seconds") or 0) for item in runs) / len(runs), 3)
