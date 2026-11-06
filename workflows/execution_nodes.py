"""Graph nodes for sandbox execution, repair, and approval."""

from __future__ import annotations

from pathlib import Path

from langgraph.graph import END
from langgraph.types import interrupt

from agents.errors import AgentError, ArtifactValidationError
from execution.approval import apply_approval_decision
from execution.fingerprint import artifact_hash
from execution.policy import ALLOWED_PYTEST_COMMAND


def execute_tests_node(state: dict) -> dict:
    from execution.project_contract import sandbox_dependency_block
    from execution.service import run_validated_tests
    from observability.logging import log_event
    from observability.metrics import record_metric

    workspace = str(state.get("workspace_path") or "")
    try:
        blocked, inspection = sandbox_dependency_block(workspace)
    except Exception as exc:
        log_event("dependency_inspection_failed", error_type=type(exc).__name__)
        blocked = f"Unsupported dependencies: {type(exc).__name__}. Sandbox execution was not started."
        inspection = {}
    try:
        record_metric(
            metric_name="dependency_inspection",
            run_id=str(state.get("run_id") or ""),
            db_path=str(state.get("db_path") or ""),
            detail=inspection,
        )
    except Exception as exc:
        log_event("dependency_inspection_failed", error_type=type(exc).__name__)
    if blocked:
        return {
            "status": "failed",
            "lifecycle_status": "failed",
            "tests_passed": False,
            "errors": [{"agent": "test_execution", "message": blocked}],
        }

    result = run_validated_tests(workspace, str(state.get("run_id") or ""))
    result = {**result, "dependency_inspection": inspection}
    command = tuple(result.get("command") or [])
    passed = (
        result.get("kind") == "passed"
        and result.get("sandbox_enforced") is True
        and result.get("exit_code") == 0
        and command == ALLOWED_PYTEST_COMMAND
    )
    update: dict = {"test_results": [result]}
    if command != ALLOWED_PYTEST_COMMAND and result.get("kind") == "passed":
        result = {
            **result,
            "kind": "sandbox_error",
            "sandbox_enforced": False,
            "message": "Refusing to mark tests passed for a command that is not allowlisted.",
        }
        update["test_results"] = [result]
        passed = False
    if passed:
        update["lifecycle_status"] = "tested"
        update["tests_passed"] = True
        update["last_failure_signature"] = ""
        return update

    signature = str(result.get("failure_signature") or "")
    attempts = int(state.get("repair_attempts") or 0)
    maximum = int(state.get("max_repair_attempts") or 0)
    repeated = bool(signature) and signature == state.get("last_failure_signature") and attempts > 0
    update["last_failure_signature"] = signature
    if result.get("kind") == "failed" and not repeated and attempts < maximum:
        update["lifecycle_status"] = "repair"
        return update

    if result.get("kind") == "timeout":
        reason = "Sandbox execution timed out."
    elif result.get("kind") == "sandbox_error":
        reason = result.get("message") or "Sandbox controls could not be enforced."
    elif repeated:
        reason = "Repair stopped because the same sandbox failure repeated."
    elif attempts >= maximum:
        reason = "Repair stopped because the retry budget is exhausted."
    else:
        reason = result.get("message") or "Sandbox tests failed and cannot be repaired."
    update["status"] = "failed"
    update["lifecycle_status"] = "failed"
    update["tests_passed"] = False
    update["errors"] = [{"agent": "test_execution", "message": str(reason)}]
    return update


def repair_node(state: dict) -> dict:
    from execution.repair import apply_model_repair

    try:
        record = apply_model_repair(state)
    except ArtifactValidationError as exc:
        return {
            "status": "failed",
            "lifecycle_status": "failed",
            "errors": [exc.to_record()],
            "validation_results": [exc.validation_record()],
        }
    except AgentError as exc:
        return {
            "status": "failed",
            "lifecycle_status": "failed",
            "errors": [exc.to_record()],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "lifecycle_status": "failed",
            "errors": [{"agent": "Repair", "message": f"{type(exc).__name__}: {exc}"}],
        }
    return {
        "repair_attempts": int(state.get("repair_attempts") or 0) + 1,
        "repair_history": [record],
        "lifecycle_status": "validated",
    }


def prepare_approval_node(state: dict) -> dict:
    try:
        digest = artifact_hash(Path(str(state.get("workspace_path") or "")))
    except OSError as exc:
        return {
            "status": "failed",
            "lifecycle_status": "failed",
            "errors": [{"agent": "approval", "message": f"Unable to hash artifacts: {exc}"}],
        }
    if not state.get("tests_passed"):
        return {
            "status": "failed",
            "lifecycle_status": "failed",
            "errors": [{"agent": "approval", "message": "Refusing to request approval without a sandbox pass."}],
        }
    return {
        "status": "awaiting_approval",
        "lifecycle_status": "awaiting_approval",
        "artifact_hash": digest,
    }


def approval_node(state: dict) -> dict:
    decision = interrupt(
        {
            "run_id": state.get("run_id"),
            "artifact_hash": state.get("artifact_hash"),
            "tests_passed": bool(state.get("tests_passed")),
        }
    )
    return apply_approval_decision(state, decision)


def route_after_tests(state: dict) -> str:
    if state.get("status") == "failed":
        return END
    if state.get("lifecycle_status") == "tested":
        return "reviewer"
    if state.get("lifecycle_status") == "repair":
        return "repair"
    return END


def route_after_repair(state: dict) -> str:
    if state.get("status") == "failed":
        return END
    return "execute_tests"
