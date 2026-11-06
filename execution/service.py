"""Workflow entry for one sandbox test run."""

from __future__ import annotations

from pathlib import Path

from execution.results import TestExecutionResult, normalize_test_result
from execution.sandbox import execute_pytest


def run_validated_tests(workspace_path: str, run_id: str) -> dict:
    """Execute pytest for a run and return a JSON-ready result."""
    try:
        result = execute_pytest(Path(workspace_path), run_id)
    except Exception as exc:
        result = TestExecutionResult(
            kind="sandbox_error",
            message=f"Sandbox execution failed: {type(exc).__name__}: {exc}",
            sandbox_enforced=False,
        )
    return normalize_test_result(result).model_dump()
