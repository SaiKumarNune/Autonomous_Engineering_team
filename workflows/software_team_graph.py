"""Software-team workflow.

Seven agents run in order. Backend, Frontend, QA, and DevOps artifacts are
validated and written into generated_projects/{run_id}/ before the next agent
sees them. Generated tests then run in the sandbox. A repair node may patch
existing Python files and re-run those tests. The run stops for human approval
before anyone can treat the project as verified.
"""

from __future__ import annotations

import json
import operator
import time
import uuid
from pathlib import Path
from typing import Annotated, Callable, Literal, TypedDict

from langgraph.errors import GraphBubbleUp, GraphInterrupt
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from agents.artifacts import ArtifactValidationResult
from agents.architect_agent import run_architect_agent
from agents.backend_engineer_agent import (
    backend_sqlite_repair_request,
    run_backend_engineer_agent,
)
from agents.devops_engineer_agent import run_devops_engineer_agent
from agents.errors import AgentError, AgentErrorRecord, ArtifactValidationError
from agents.fast_mode import fast_dev_mode
from agents.frontend_engineer_agent import (
    accept_frontend_artifacts,
    frontend_syntax_repair_request,
    run_frontend_engineer_agent,
)
from agents.product_manager_agent import run_product_manager_agent
from agents.qa_engineer_agent import run_qa_engineer_agent
from agents.reviewer_agent import run_reviewer_agent
from database.checkpoint import CheckpointIntegrityError, SqliteCheckpointSaver
from database.repository import default_db_path, persist_workflow_state
from execution.dependencies import inspect_requirements
from execution.project_contract import (
    backend_contract_issues,
    normalize_backend_requirements,
    qa_contract_issues,
)
from execution.settings import load_max_repair_attempts
from observability.context import reset_run_context, set_run_context
from observability.logging import configure_logging, log_event
from observability.metrics import record_metric, summarize_metrics
from reliability.checks import validate_checkpoint, verify_artifact_consistency
from reliability.locks import RunInProgressError, execution_lock
from tools.validators import validate_architecture_output, validate_requirements_output, validate_review_output
from tools.workspace import create_run_workspace, default_workspace_root
from workflows.artifact_pipeline import (
    deployment_contract,
    heuristic_record,
    render_stored_files,
    store_validated_artifacts,
)
from workflows.execution_nodes import (
    approval_node,
    execute_tests_node,
    prepare_approval_node,
    repair_node,
    route_after_repair,
    route_after_tests,
)

Status = Literal["running", "completed", "failed", "awaiting_approval", "approved"]
Lifecycle = Literal[
    "generated",
    "validated",
    "tested",
    "repair",
    "awaiting_approval",
    "approved",
    "failed",
]

OUTPUT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("Requirements", "requirements"),
    ("Architecture", "architecture"),
    ("Backend", "backend_code"),
    ("Frontend", "frontend_code"),
    ("Tests", "qa_tests"),
    ("DevOps", "devops_files"),
    ("Review", "review"),
)


class FileRecord(TypedDict):
    relative_path: str
    source_agent: str
    language: str
    size_bytes: int
    sha256: str


class SoftwareTeamState(TypedDict):
    user_request: str
    run_id: str
    workspace_path: str
    requirements: str
    architecture: str
    backend_code: str
    frontend_code: str
    qa_tests: str
    devops_files: str
    review: str
    status: Status
    lifecycle_status: Lifecycle
    db_path: str
    artifact_hash: str
    tests_passed: bool
    repair_attempts: int
    max_repair_attempts: int
    last_failure_signature: str
    errors: Annotated[list[AgentErrorRecord], operator.add]
    artifact_manifest: Annotated[list[FileRecord], operator.add]
    validation_results: Annotated[list[dict], operator.add]
    test_results: Annotated[list[dict], operator.add]
    repair_history: Annotated[list[dict], operator.add]


class StateUpdate(TypedDict, total=False):
    requirements: str
    architecture: str
    backend_code: str
    frontend_code: str
    qa_tests: str
    devops_files: str
    review: str
    status: Status
    lifecycle_status: Lifecycle
    artifact_hash: str
    tests_passed: bool
    repair_attempts: int
    last_failure_signature: str
    errors: list[AgentErrorRecord]
    artifact_manifest: list[FileRecord]
    validation_results: list[dict]
    test_results: list[dict]
    repair_history: list[dict]


def _require_text(value: object, agent_name: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentError(agent_name, f"{field_name} cannot be empty.")
    return value.strip()


def _failure(exc: Exception) -> StateUpdate:
    if isinstance(exc, AgentError):
        record = exc.to_record()
    else:
        record = {"agent": "workflow", "message": f"{type(exc).__name__}: {exc}"}
    update: StateUpdate = {
        "status": "failed",
        "lifecycle_status": "failed",
        "errors": [record],
    }
    if isinstance(exc, ArtifactValidationError):
        update["validation_results"] = [exc.validation_record()]
    return update


def _guard(producer: Callable[[], str], output_key: str) -> StateUpdate:
    try:
        value = producer()
    except Exception as exc:
        return _failure(exc)
    if not isinstance(value, str) or not value.strip():
        return _failure(AgentError(output_key, "Agent returned empty output."))
    return {output_key: value}


def _manifest_paths(state: SoftwareTeamState) -> list[str]:
    return [str(item.get("relative_path", "")) for item in state.get("artifact_manifest") or []]


def _sqlite_configuration_missing(issues: list[str]) -> bool:
    return any(issue == "missing SQLite configuration." for issue in issues)


def _orm_annotation_rejected(issues: list[str]) -> bool:
    return any("SQLAlchemy model" in issue for issue in issues)


def _reject_contract(agent_name: str, issues: list[str]) -> None:
    if issues:
        raise ArtifactValidationError(
            agent_name,
            ArtifactValidationResult(valid=False, issues=issues),
        )


def _store(state: SoftwareTeamState, agent_name: str, artifacts: object) -> dict:
    return store_validated_artifacts(
        state["workspace_path"],
        agent_name,
        artifacts,  # type: ignore[arg-type]
        _manifest_paths(state),
    )


def product_manager_node(state: SoftwareTeamState) -> StateUpdate:
    def produce() -> str:
        request = _require_text(state.get("user_request"), "Product Manager", "user_request")
        return run_product_manager_agent(request)

    update = _guard(produce, "requirements")
    if update.get("status") == "failed":
        return update
    update["validation_results"] = [
        heuristic_record("Product Manager", validate_requirements_output(update["requirements"]))
    ]
    return update


def architect_node(state: SoftwareTeamState) -> StateUpdate:
    def produce() -> str:
        requirements = _require_text(state.get("requirements"), "Architect", "requirements")
        return run_architect_agent(requirements)

    update = _guard(produce, "architecture")
    if update.get("status") == "failed":
        return update
    update["validation_results"] = [
        heuristic_record("Architect", validate_architecture_output(update["architecture"]))
    ]
    return update


def backend_node(state: SoftwareTeamState) -> StateUpdate:
    try:
        architecture = _require_text(state.get("architecture"), "Backend Engineer", "architecture")
        requirements = _require_text(state.get("requirements"), "Backend Engineer", "requirements")
        artifacts = run_backend_engineer_agent(architecture, requirements)
        normalize_backend_requirements(artifacts)
        issues = backend_contract_issues(artifacts)
        if _sqlite_configuration_missing(issues) or _orm_annotation_rejected(issues):
            log_event(
                "backend_sqlite_repair",
                agent_name="Backend Engineer",
                run_id=state.get("run_id"),
            )
            artifacts = run_backend_engineer_agent(
                architecture,
                requirements,
                correction=backend_sqlite_repair_request(issues, artifacts),
            )
            normalize_backend_requirements(artifacts)
            issues = backend_contract_issues(artifacts)
        _reject_contract("Backend Engineer", issues)
        stored = _store(state, "Backend Engineer", artifacts)
    except Exception as exc:
        return _failure(exc)
    return {
        "backend_code": str(stored["summary"]),
        "artifact_manifest": stored["records"],  # type: ignore[typeddict-item]
        "validation_results": [stored["validation"]],  # type: ignore[list-item]
    }


def frontend_node(state: SoftwareTeamState) -> StateUpdate:
    try:
        architecture = _require_text(state.get("architecture"), "Frontend Engineer", "architecture")
        requirements = _require_text(state.get("requirements"), "Frontend Engineer", "requirements")
        backend_artifacts = render_stored_files(
            state["workspace_path"],
            list(state.get("artifact_manifest") or []),
            {"Backend Engineer"},
        )
        first = run_frontend_engineer_agent(
            architecture,
            requirements,
            backend_artifacts,
            user_request=str(state.get("user_request") or ""),
        )
        try:
            accept_frontend_artifacts(first)
            stored = _store(state, "Frontend Engineer", first)
        except ArtifactValidationError as exc:
            if "Python syntax error" not in exc.message:
                raise
            log_event(
                "frontend_syntax_repair",
                agent_name="Frontend Engineer",
                run_id=state.get("run_id"),
            )
            corrected = run_frontend_engineer_agent(
                architecture,
                requirements,
                backend_artifacts,
                correction=frontend_syntax_repair_request(exc.message, first),
                user_request=str(state.get("user_request") or ""),
            )
            try:
                accept_frontend_artifacts(corrected)
            except ArtifactValidationError as repair_exc:
                raise ArtifactValidationError(
                    "Frontend Engineer",
                    ArtifactValidationResult(
                        valid=False,
                        issues=[
                            "Frontend syntax repair failed. Invalid Python was not written. "
                            + repair_exc.message
                        ],
                    ),
                ) from repair_exc
            stored = _store(state, "Frontend Engineer", corrected)
    except Exception as exc:
        return _failure(exc)
    return {
        "frontend_code": str(stored["summary"]),
        "artifact_manifest": stored["records"],  # type: ignore[typeddict-item]
        "validation_results": [stored["validation"]],  # type: ignore[list-item]
    }


def qa_node(state: SoftwareTeamState) -> StateUpdate:
    try:
        requirements = _require_text(state.get("requirements"), "QA Engineer", "requirements")
        generated_code = render_stored_files(
            state["workspace_path"],
            list(state.get("artifact_manifest") or []),
            {"Backend Engineer", "Frontend Engineer"},
        )
        artifacts = run_qa_engineer_agent(generated_code, requirements)
        local_modules: set[str] = set()
        for path in [*_manifest_paths(state), *(item.relative_path for item in artifacts.files)]:
            file_path = Path(path)
            local_modules.add(file_path.stem)
            local_modules.update(part for part in file_path.parts[:-1] if part not in {"", "."})
        _reject_contract("QA Engineer", qa_contract_issues(artifacts, local_modules))
        stored = _store(state, "QA Engineer", artifacts)
    except Exception as exc:
        return _failure(exc)
    return {
        "qa_tests": str(stored["summary"]),
        "artifact_manifest": stored["records"],  # type: ignore[typeddict-item]
        "validation_results": [stored["validation"]],  # type: ignore[list-item]
    }


def devops_node(state: SoftwareTeamState) -> StateUpdate:
    try:
        requirements = _require_text(state.get("requirements"), "DevOps Engineer", "requirements")
        architecture = _require_text(state.get("architecture"), "DevOps Engineer", "architecture")
        manifest = json.dumps(state.get("artifact_manifest") or [], indent=2)
        files = render_stored_files(
            state["workspace_path"],
            list(state.get("artifact_manifest") or []),
            {"Backend Engineer", "Frontend Engineer", "QA Engineer"},
        )
        contract = deployment_contract(
            state["workspace_path"],
            list(state.get("artifact_manifest") or []),
        )
        project_context = (
            f"REQUIREMENTS:\n{requirements}\n\n"
            f"ARCHITECTURE:\n{architecture}\n\n"
            f"{contract}\n\n"
            f"ARTIFACT MANIFEST:\n{manifest}\n\n"
            f"STORED FILES:\n{files}"
        )
        stored = _store(
            state,
            "DevOps Engineer",
            run_devops_engineer_agent(
                project_context,
                workspace_path=state["workspace_path"],
                manifest=list(state.get("artifact_manifest") or []),
            ),
        )
    except Exception as exc:
        return _failure(exc)
    return {
        "devops_files": str(stored["summary"]),
        "artifact_manifest": stored["records"],  # type: ignore[typeddict-item]
        "validation_results": [stored["validation"]],  # type: ignore[list-item]
        "lifecycle_status": "validated",
    }


def reviewer_node(state: SoftwareTeamState) -> StateUpdate:
    def produce() -> str:
        requirements = _require_text(state.get("requirements"), "Reviewer", "requirements")
        architecture = _require_text(state.get("architecture"), "Reviewer", "architecture")
        backend_code = _require_text(state.get("backend_code"), "Reviewer", "backend_code")
        frontend_code = _require_text(state.get("frontend_code"), "Reviewer", "frontend_code")
        qa_tests = _require_text(state.get("qa_tests"), "Reviewer", "qa_tests")
        devops_files = _require_text(state.get("devops_files"), "Reviewer", "devops_files")
        manifest = json.dumps(state.get("artifact_manifest") or [], indent=2)
        validation = json.dumps(state.get("validation_results") or [], indent=2)
        tests = json.dumps(state.get("test_results") or [], indent=2)[:4000]
        repairs = json.dumps(state.get("repair_history") or [], indent=2)[:4000]
        all_outputs = (
            f"REQUIREMENTS:\n{requirements}\n\n"
            f"ARCHITECTURE:\n{architecture}\n\n"
            f"BACKEND SUMMARY:\n{backend_code}\n\n"
            f"FRONTEND SUMMARY:\n{frontend_code}\n\n"
            f"QA SUMMARY:\n{qa_tests}\n\n"
            f"DEVOPS SUMMARY:\n{devops_files}\n\n"
            f"ARTIFACT MANIFEST:\n{manifest}\n\n"
            f"VALIDATION RESULTS:\n{validation}\n\n"
            f"SANDBOX TEST RESULTS:\n{tests}\n\n"
            f"REPAIR HISTORY:\n{repairs}"
        )
        return run_reviewer_agent(all_outputs)

    update = _guard(produce, "review")
    if update.get("status") == "failed":
        return update
    update["validation_results"] = [
        heuristic_record("Reviewer", validate_review_output(str(update["review"])))
    ]
    return update


def _continue_to(next_node: str) -> Callable[[SoftwareTeamState], str]:
    def route(state: SoftwareTeamState) -> str:
        if state.get("status") == "failed":
            return END
        return next_node

    route.__name__ = f"route_to_{next_node}"
    return route


def build_software_team_graph(checkpointer=None):
    """Compile the team graph, including the sandbox repair loop and approval gate."""
    graph = StateGraph(SoftwareTeamState)

    graph.add_node("product_manager", _observe("product_manager", product_manager_node))
    graph.add_node("architect", _observe("architect", architect_node))
    graph.add_node("backend_engineer", _observe("backend_engineer", backend_node))
    graph.add_node("frontend_engineer", _observe("frontend_engineer", frontend_node))
    graph.add_node("qa_engineer", _observe("qa_engineer", qa_node))
    graph.add_node("devops_engineer", _observe("devops_engineer", devops_node))
    graph.add_node("execute_tests", _observe("execute_tests", execute_tests_node))
    graph.add_node("repair", _observe("repair", repair_node))
    graph.add_node("reviewer", _observe("reviewer", reviewer_node))
    graph.add_node("prepare_approval", _observe("prepare_approval", prepare_approval_node))
    graph.add_node("approval", _observe("approval", approval_node))

    graph.set_entry_point("product_manager")
    graph.add_conditional_edges("product_manager", _continue_to("architect"))
    graph.add_conditional_edges("architect", _continue_to("backend_engineer"))
    graph.add_conditional_edges("backend_engineer", _continue_to("frontend_engineer"))
    graph.add_conditional_edges("frontend_engineer", _continue_to("qa_engineer"))
    graph.add_conditional_edges("qa_engineer", _continue_to("devops_engineer"))
    graph.add_conditional_edges("devops_engineer", _continue_to("execute_tests"))
    graph.add_conditional_edges("execute_tests", route_after_tests)
    graph.add_conditional_edges("repair", route_after_repair)
    graph.add_conditional_edges("reviewer", _continue_to("prepare_approval"))
    graph.add_conditional_edges("prepare_approval", _continue_to("approval"))
    graph.add_edge("approval", END)

    return graph.compile(checkpointer=checkpointer)


def _initial_state(
    user_request: str,
    run_id: str,
    workspace_path: str,
    *,
    db_path: str,
    max_repair_attempts: int,
) -> SoftwareTeamState:
    return {
        "user_request": user_request,
        "run_id": run_id,
        "workspace_path": workspace_path,
        "requirements": "",
        "architecture": "",
        "backend_code": "",
        "frontend_code": "",
        "qa_tests": "",
        "devops_files": "",
        "review": "",
        "status": "running",
        "lifecycle_status": "generated",
        "db_path": db_path,
        "artifact_hash": "",
        "tests_passed": False,
        "repair_attempts": 0,
        "max_repair_attempts": max_repair_attempts,
        "last_failure_signature": "",
        "errors": [],
        "artifact_manifest": [],
        "validation_results": [],
        "test_results": [],
        "repair_history": [],
    }


def _observe(stage: str, node):
    def wrapped(state):
        started = time.perf_counter()
        try:
            update = node(state)
        except GraphBubbleUp:
            _safe_stage(state, stage, int((time.perf_counter() - started) * 1000), failed=False)
            raise
        except Exception:
            _safe_stage(state, stage, int((time.perf_counter() - started) * 1000), failed=True)
            raise
        _safe_stage(state, stage, int((time.perf_counter() - started) * 1000), update)
        return update

    wrapped.__name__ = getattr(node, "__name__", stage)
    return wrapped


def _safe_stage(state, stage: str, duration_ms: int, update: dict | None = None, failed: bool = False) -> None:
    try:
        detail = {"failed": failed}
        if isinstance(update, dict):
            detail["status"] = update.get("status", state.get("status"))
            if stage == "execute_tests":
                results = update.get("test_results") or []
                if results and isinstance(results[-1], dict):
                    record_metric(
                        metric_name="sandbox_execution",
                        run_id=str(state.get("run_id") or ""),
                        db_path=str(state.get("db_path") or ""),
                        duration_ms=results[-1].get("duration_ms"),
                        detail={
                            "kind": results[-1].get("kind"),
                            "exit_code": results[-1].get("exit_code"),
                            "sandbox_enforced": results[-1].get("sandbox_enforced"),
                        },
                    )
            if stage == "repair":
                record_metric(
                    metric_name="repair_attempt",
                    run_id=str(state.get("run_id") or ""),
                    db_path=str(state.get("db_path") or ""),
                    duration_ms=duration_ms,
                    detail={"status": update.get("status", "applied")},
                )
        record_metric(
            metric_name="stage",
            agent_name=stage,
            duration_ms=duration_ms,
            run_id=str(state.get("run_id") or ""),
            db_path=str(state.get("db_path") or ""),
            detail=detail,
        )
    except Exception as exc:
        log_event("metric_write_failed", stage=stage, error_type=type(exc).__name__)


def run_software_team_workflow(
    user_request: str,
    *,
    workspace_root: Path | str | None = None,
    db_path: Path | str | None = None,
    run_id: str | None = None,
) -> dict:
    """Run the software team once, or resume the saved checkpoint for this run id."""
    configure_logging()
    selected_run_id = run_id or uuid.uuid4().hex
    root = Path(workspace_root) if workspace_root is not None else default_workspace_root()
    database = str(db_path) if db_path is not None else default_db_path()
    try:
        maximum = load_max_repair_attempts()
    except ValueError as exc:
        failed = _failed_settings(user_request, selected_run_id, database, str(exc))
        return _persist(database, failed)
    try:
        with execution_lock(database, selected_run_id):
            return _run_locked(
                user_request,
                root=root,
                database=database,
                run_id=selected_run_id,
                maximum=maximum,
            )
    except RunInProgressError as exc:
        log_event("concurrent_run_rejected", run_id=selected_run_id)
        return {
            "run_id": selected_run_id,
            "status": "failed",
            "lifecycle_status": "failed",
            "errors": [{"agent": "workflow", "message": str(exc)}],
        }


def resume_software_team_workflow(
    run_id: str,
    *,
    approved: bool,
    artifact_hash: str,
    db_path: Path | str | None = None,
) -> dict:
    """Resume the approval gate for one saved run."""
    configure_logging()
    database = str(db_path) if db_path is not None else default_db_path()
    decision = {"approved": approved is True, "run_id": run_id, "artifact_hash": artifact_hash}
    try:
        with execution_lock(database, run_id):
            integrity = validate_checkpoint(database)
            if not integrity["valid"]:
                raise CheckpointIntegrityError("; ".join(integrity["issues"]))
            final_state = _invoke_command(database, run_id, Command(resume=decision))
    except RunInProgressError as exc:
        return {
            "run_id": run_id,
            "status": "failed",
            "lifecycle_status": "failed",
            "errors": [{"agent": "workflow", "message": str(exc)}],
        }
    except Exception as exc:
        return {
            "run_id": run_id,
            "status": "failed",
            "lifecycle_status": "failed",
            "errors": [{"agent": "approval", "message": str(exc)}],
        }
    return _persist(database, final_state)


def _run_locked(user_request: str, *, root: Path, database: str, run_id: str, maximum: int) -> dict:
    integrity = validate_checkpoint(database)
    if not integrity["valid"]:
        failed = _initial_state(user_request, run_id, "", db_path=database, max_repair_attempts=maximum)
        failed["status"] = "failed"
        failed["lifecycle_status"] = "failed"
        failed["errors"] = [
            {"agent": "checkpoint", "message": " ".join(str(item) for item in integrity["issues"])}
        ]
        return _persist(database, failed)
    existing = _checkpoint_state(database, run_id)
    if existing is not None:
        log_event("run_resumed", run_id=run_id, status=existing.get("status"))
        next_nodes = _checkpoint_next(database, run_id)
        if not next_nodes or next_nodes == ("approval",):
            return _annotate(database, existing)
        resumed = _invoke_resume(database, run_id, existing)
        return _persist(database, resumed)

    try:
        workspace = create_run_workspace(run_id, root)
    except Exception as exc:
        failed = _initial_state(user_request, run_id, "", db_path=database, max_repair_attempts=maximum)
        failed["status"] = "failed"
        failed["lifecycle_status"] = "failed"
        failed["errors"] = [{"agent": "workspace", "message": str(exc)}]
        return _persist(database, failed)

    initial = _initial_state(
        user_request,
        run_id,
        str(workspace),
        db_path=database,
        max_repair_attempts=maximum,
    )
    final_state = _invoke(database, initial, run_id)
    return _persist(database, final_state)


def _failed_settings(user_request: str, run_id: str, database: str, message: str) -> dict:
    failed = _initial_state(user_request, run_id, "", db_path=database, max_repair_attempts=0)
    failed["status"] = "failed"
    failed["lifecycle_status"] = "failed"
    failed["errors"] = [{"agent": "settings", "message": message}]
    return failed


def _invoke(database: str, initial: SoftwareTeamState, run_id: str) -> dict:
    tokens = set_run_context(run_id, database)
    config = {"configurable": {"thread_id": run_id}}
    try:
        saver = SqliteCheckpointSaver(database)
        graph = build_software_team_graph(checkpointer=saver)
        return _finish_invoke(graph.invoke(initial, config, durability="sync"))
    except GraphInterrupt:
        saver = SqliteCheckpointSaver(database)
        snapshot = build_software_team_graph(checkpointer=saver).get_state(config)
        return _finish_invoke(dict(snapshot.values))
    except Exception as exc:
        failed = dict(initial)
        failed["status"] = "failed"
        failed["lifecycle_status"] = "failed"
        failed["errors"] = [{"agent": "workflow", "message": f"{type(exc).__name__}: {exc}"}]
        return failed
    finally:
        reset_run_context(tokens)


def _invoke_resume(database: str, run_id: str, fallback: dict) -> dict:
    tokens = set_run_context(run_id, database)
    config = {"configurable": {"thread_id": run_id}}
    try:
        saver = SqliteCheckpointSaver(database)
        graph = build_software_team_graph(checkpointer=saver)
        return _finish_invoke(graph.invoke(None, config, durability="sync"))
    except GraphInterrupt:
        saver = SqliteCheckpointSaver(database)
        snapshot = build_software_team_graph(checkpointer=saver).get_state(config)
        return _finish_invoke(dict(snapshot.values))
    except Exception as exc:
        failed = dict(fallback)
        failed["status"] = "failed"
        failed["lifecycle_status"] = "failed"
        errors = list(failed.get("errors") or [])
        errors.append({"agent": "workflow", "message": f"{type(exc).__name__}: {exc}"})
        failed["errors"] = errors
        return failed
    finally:
        reset_run_context(tokens)


def _invoke_command(database: str, run_id: str, command: Command) -> dict:
    tokens = set_run_context(run_id, database)
    config = {"configurable": {"thread_id": run_id}}
    try:
        saver = SqliteCheckpointSaver(database)
        graph = build_software_team_graph(checkpointer=saver)
        return _finish_invoke(graph.invoke(command, config, durability="sync"))
    finally:
        reset_run_context(tokens)


def _checkpoint_state(database: str, run_id: str) -> dict | None:
    if not validate_checkpoint(database)["present"]:
        return None
    try:
        saver = SqliteCheckpointSaver(database)
        snapshot = build_software_team_graph(checkpointer=saver).get_state(
            {"configurable": {"thread_id": run_id}}
        )
    except CheckpointIntegrityError:
        raise
    values = dict(getattr(snapshot, "values", {}) or {})
    if not values.get("run_id"):
        return None
    return values


def _checkpoint_next(database: str, run_id: str) -> tuple:
    saver = SqliteCheckpointSaver(database)
    snapshot = build_software_team_graph(checkpointer=saver).get_state(
        {"configurable": {"thread_id": run_id}}
    )
    return tuple(getattr(snapshot, "next", ()) or ())


def _finish_invoke(state: dict) -> dict:
    state.pop("__interrupt__", None)
    return state


def _persist(database: str, state: dict) -> dict:
    try:
        persist_workflow_state(state, database)
    except Exception as exc:
        errors = list(state.get("errors") or [])
        errors.append({"agent": "persistence", "message": str(exc)})
        state["errors"] = errors
        state["status"] = "failed"
    return _annotate(database, state)


def _annotate(database: str, state: dict) -> dict:
    state["execution_mode"] = "simulated" if fast_dev_mode() else "ollama"
    state["run_metrics"] = summarize_metrics(database, str(state.get("run_id") or ""))
    state["consistency"] = verify_artifact_consistency(state)
    workspace = state.get("workspace_path") or ""
    if workspace:
        try:
            state["dependency_inspection"] = inspect_requirements(workspace)
        except OSError as exc:
            state["dependency_inspection"] = {"requirements_present": False, "error": str(exc)}
    try:
        record_metric(
            metric_name="run_summary",
            run_id=str(state.get("run_id") or ""),
            db_path=database,
            detail={
                "lifecycle_status": state.get("lifecycle_status"),
                "status": state.get("status"),
                "artifact_count": len(state.get("artifact_manifest") or []),
                "validation_failures": sum(
                    1
                    for item in state.get("validation_results") or []
                    if isinstance(item, dict) and item.get("blocking") and not item.get("valid")
                ),
                "repair_attempts": state.get("repair_attempts", 0),
                "tests_passed": bool(state.get("tests_passed")),
                "consistent": bool((state.get("consistency") or {}).get("consistent")),
            },
        )
    except Exception as exc:
        log_event("metric_write_failed", metric_name="run_summary", error_type=type(exc).__name__)
    log_event(
        "run_finished",
        run_id=state.get("run_id"),
        status=state.get("status"),
        lifecycle_status=state.get("lifecycle_status"),
        artifact_count=len(state.get("artifact_manifest") or []),
        repair_attempts=state.get("repair_attempts", 0),
    )
    return state
