import json
import sqlite3

import httpx

from agents.errors import AgentError
from agents.llm import extract_token_usage, invoke_agent
from database.setup_db import create_database
from evals.benchmark import summarize_benchmark
from execution.dependencies import inspect_requirements
from observability.context import reset_run_context, set_run_context
from observability.metrics import record_metric, summarize_metrics
from reliability.checks import validate_checkpoint, verify_artifact_consistency
from reliability.locks import acquire_execution_lock
from tests.test_workflow import _Calls, _patch_agents, _run
import workflows.software_team_graph as workflow
from tools.workspace import create_run_workspace, write_text_atomic


def test_transport_failures_are_retried_and_other_errors_are_not(monkeypatch):
    calls = {"n": 0}

    class FakeChat:
        def __init__(self, **_kwargs):
            return None

        def invoke(self, _messages):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("temporary disconnect")
            return type("Response", (), {"content": "Requirements for the tracker", "response_metadata": {}})()

    monkeypatch.setattr("agents.llm.ChatOllama", FakeChat)
    monkeypatch.setattr("agents.llm.time.sleep", lambda _seconds: None)
    text = invoke_agent("Product Manager", "system", "Build a tracker")

    assert text == "Requirements for the tracker"
    assert calls["n"] == 2

    calls["n"] = 0

    class Broken:
        def __init__(self, **_kwargs):
            return None

        def invoke(self, _messages):
            calls["n"] += 1
            raise RuntimeError("schema mismatch")

    monkeypatch.setattr("agents.llm.ChatOllama", Broken)
    try:
        invoke_agent("Product Manager", "system", "Build a tracker")
    except AgentError as exc:
        assert "schema mismatch" in exc.message
    assert calls["n"] == 1


def test_token_metrics_stay_empty_when_the_model_omits_usage(tmp_path):
    class Response:
        content = "hello"
        response_metadata = {"model": "llama3.1:8b"}

    usage = extract_token_usage(Response())
    assert usage["reported"] is False
    assert usage["prompt_tokens"] is None
    assert usage["total_tokens"] is None

    reported = extract_token_usage(
        type(
            "Response",
            (),
            {
                "content": "hello",
                "response_metadata": {"prompt_eval_count": 11, "eval_count": 7},
                "usage_metadata": {},
            },
        )()
    )
    assert reported["reported"] is True
    assert reported["prompt_tokens"] == 11
    assert reported["completion_tokens"] == 7
    assert reported["total_tokens"] == 18
    assert reported["total_tokens_source"] == "sum_of_reported_counts"

    database = tmp_path / "metrics.db"
    tokens = set_run_context("runmetric1", str(database))
    try:
        record_metric(
            metric_name="model_invocation",
            agent_name="Product Manager",
            duration_ms=15,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            detail={"token_usage_reported": False},
        )
    finally:
        reset_run_context(tokens)
    summary = summarize_metrics(str(database), "runmetric1")
    assert summary["token_usage"]["reported"] is False
    assert summary["token_usage"]["total_tokens"] is None


def test_requirements_outside_the_allowlist_are_not_installed(tmp_path):
    workspace = create_run_workspace("rundeps001", tmp_path)
    write_text_atomic(
        workspace,
        "requirements.txt",
        "fastapi==0.115.12\nflask==1.0.0\npytest\n",
    )
    report = inspect_requirements(workspace)
    assert report["approved"] == ["fastapi==0.115.12"]
    assert "flask==1.0.0" in report["unapproved"]
    assert "pytest" in report["unpinned_or_mismatched"]


def test_corrupt_checkpoint_does_not_start_agents(monkeypatch, tmp_path):
    database = tmp_path / "team.db"
    create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO graph_checkpoints (id, payload) VALUES (1, ?)",
            ("{",),
        )
    calls = _Calls()
    _patch_agents(monkeypatch, calls)
    state = workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated",
        db_path=database,
        run_id="runcorrupt",
    )
    assert calls == []
    assert state["status"] == "failed"
    assert state["errors"][0]["agent"] == "checkpoint"
    assert validate_checkpoint(str(database))["valid"] is False


def test_second_start_reuses_the_approval_checkpoint(monkeypatch, tmp_path):
    calls, first = _run(monkeypatch, tmp_path)
    again = _Calls()
    _patch_agents(monkeypatch, again)
    second = workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated",
        db_path=tmp_path / "team.db",
        run_id=first["run_id"],
    )
    assert again == []
    assert second["status"] == "awaiting_approval"
    assert second["artifact_hash"] == first["artifact_hash"]
    assert calls == [
        "product_manager",
        "architect",
        "backend",
        "frontend",
        "qa",
        "devops",
        "reviewer",
    ]


def test_concurrent_run_is_rejected(monkeypatch, tmp_path):
    database = tmp_path / "team.db"
    create_database(database)
    acquire_execution_lock(str(database), "runholder1")
    calls = _Calls()
    _patch_agents(monkeypatch, calls)
    state = workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated",
        db_path=database,
        run_id="runother01",
    )
    assert calls == []
    assert state["status"] == "failed"
    assert "already in progress" in state["errors"][0]["message"]


def test_consistency_detects_a_missing_manifest_file(tmp_path):
    workspace = create_run_workspace("runconsist", tmp_path)
    write_text_atomic(workspace, "backend/main.py", "print('ok')\n")
    state = {
        "workspace_path": str(workspace),
        "artifact_hash": "",
        "artifact_manifest": [
            {"relative_path": "backend/main.py", "sha256": "0" * 64},
            {"relative_path": "missing.py", "sha256": ""},
        ],
    }
    report = verify_artifact_consistency(state)
    assert report["consistent"] is False
    assert any("missing.py" in issue for issue in report["issues"])


def test_partial_agent_failure_is_not_generation_success():
    from evals.benchmark import measure_run

    measured = measure_run(
        {"task_id": "partial", "request": "unused"},
        lambda _request, run_id: {
            "run_id": run_id,
            "status": "failed",
            "artifact_manifest": [{"relative_path": "backend/main.py"}],
            "errors": [{"agent": "Frontend Engineer", "message": "syntax error"}],
            "validation_results": [],
            "test_results": [],
            "repair_attempts": 0,
        },
        "runpartial",
    )
    assert measured["execution"] == "ollama"
    assert measured["generation_succeeded"] is False
    assert measured["artifact_validation_succeeded"] is False
    assert measured["sandbox_passed"] is False


def test_benchmark_summary_uses_only_real_runs():
    summary = summarize_benchmark(
        [
            {
                "execution": "mocked",
                "generation_succeeded": True,
                "artifact_validation_succeeded": True,
                "sandbox_passed": True,
                "repair_attempted": False,
                "approval_ready": True,
                "duration_seconds": 1,
            },
            {
                "execution": "ollama",
                "generation_succeeded": True,
                "artifact_validation_succeeded": True,
                "sandbox_passed": False,
                "repair_attempted": True,
                "repair_succeeded": False,
                "approval_ready": False,
                "duration_seconds": 10,
            },
        ]
    )
    assert summary["tasks"] == 1
    assert summary["generation_success_rate"] == 1.0
    assert summary["sandbox_test_pass_rate"] == 0.0
    assert summary["repair_success_rate"] == 0.0
    assert summary["approval_ready_projects"] == 0
    assert summary["average_duration_seconds"] == 10
    assert summary["sandbox_pass_does_not_prove_correctness"] is True


def test_workflow_persists_stage_metrics(monkeypatch, tmp_path):
    _calls, state = _run(monkeypatch, tmp_path)
    with sqlite3.connect(tmp_path / "team.db") as connection:
        stages = connection.execute(
            "SELECT agent_name FROM run_metrics WHERE metric_name = 'stage' ORDER BY id"
        ).fetchall()
    names = [row[0] for row in stages]
    assert "product_manager" in names
    assert "execute_tests" in names
    assert "reviewer" in names
    assert state["run_metrics"]["token_usage"]["reported"] is False
    summary = json.loads(json.dumps(state["run_metrics"]))
    assert summary["token_usage"]["total_tokens"] is None
