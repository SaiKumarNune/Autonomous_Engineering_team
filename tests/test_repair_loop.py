import hashlib
import sqlite3
from pathlib import Path

import pytest

from execution.repair import RepairPatch, RepairProposal, apply_proposal, replace_text_atomic
from execution.settings import load_max_repair_attempts
from tests.test_workflow import _BACKEND_SOURCE, _Calls, _patch_agents
from tools.workspace import WorkspaceSecurityError, create_run_workspace, write_text_atomic
import workflows.software_team_graph as workflow


def _manifest():
    return [
        {"relative_path": "backend/main.py", "source_agent": "Backend Engineer", "language": "python"},
        {"relative_path": "tests/test_api.py", "source_agent": "QA Engineer", "language": "python"},
    ]


def _failure(signature: str) -> dict:
    return {
        "kind": "failed",
        "command": ["pytest", "-q", "--tb=line", "--disable-warnings", "-p", "no:cacheprovider"],
        "exit_code": 1,
        "stdout": "FAILED tests/test_api.py::test_api",
        "stderr": "E AssertionError",
        "duration_ms": 2,
        "sandbox_enforced": True,
        "failure_signature": signature,
        "diagnostics": ["FAILED tests/test_api.py::test_api"],
        "message": "Sandbox tests failed.",
    }


def _passed() -> dict:
    return {
        "kind": "passed",
        "command": ["pytest", "-q", "--tb=line", "--disable-warnings", "-p", "no:cacheprovider"],
        "exit_code": 0,
        "stdout": "1 passed",
        "stderr": "",
        "duration_ms": 2,
        "sandbox_enforced": True,
        "failure_signature": "",
        "diagnostics": [],
        "message": "Sandbox tests passed.",
    }


def _start(monkeypatch, tmp_path, tests, repair, run_id="runrepair1"):
    recorded = _Calls()
    _patch_agents(monkeypatch, recorded)
    monkeypatch.setattr("execution.service.run_validated_tests", tests)
    monkeypatch.setattr("execution.repair.request_repair_proposal", repair)
    return workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated",
        db_path=tmp_path / "team.db",
        run_id=run_id,
    )


def test_repair_loop_applies_a_patch_and_passes(monkeypatch, tmp_path):
    def tests(workspace, _run_id):
        text = Path(workspace, "backend/main.py").read_text(encoding="utf-8")
        if "FIXED" in text:
            return _passed()
        return _failure(hashlib.sha256(text.encode()).hexdigest())

    def repair(_context):
        return RepairProposal(
            summary="Fix the backend assertion.",
            files=[
                RepairPatch(
                    relative_path="backend/main.py",
                    content="FIXED = True\nprint('ok')\n",
                    language="python",
                )
            ],
        )

    state = _start(monkeypatch, tmp_path, tests, repair)

    assert state["status"] == "awaiting_approval"
    assert state["tests_passed"] is True
    assert state["repair_attempts"] == 1
    assert state["repair_history"][0]["status"] == "applied"
    assert "FIXED" in Path(state["workspace_path"], "backend/main.py").read_text(encoding="utf-8")
    assert "--- a/backend/main.py" in state["repair_history"][0]["diff"]
    with sqlite3.connect(tmp_path / "team.db") as connection:
        repair_row = connection.execute(
            "SELECT attempt, status FROM repair_attempts"
        ).fetchone()
        test_count = connection.execute("SELECT COUNT(*) FROM test_results").fetchone()[0]
    assert repair_row == (1, "applied")
    assert test_count == 2


def test_repair_stops_on_a_repeated_failure(monkeypatch, tmp_path):
    def tests(_workspace, _run_id):
        return _failure("same-failure")

    def repair(_context):
        repair.n += 1
        return RepairProposal(
            summary="Try a different print.",
            files=[RepairPatch(relative_path="backend/main.py", content=f"print({repair.n})\n")],
        )

    repair.n = 0
    state = _start(monkeypatch, tmp_path, tests, repair, run_id="runrepeat1")

    assert state["status"] == "failed"
    assert state["repair_attempts"] == 1
    assert "same sandbox failure" in state["errors"][-1]["message"]


def test_repair_stops_when_the_budget_is_exhausted(monkeypatch, tmp_path):
    def tests(workspace, _run_id):
        text = Path(workspace, "backend/main.py").read_text(encoding="utf-8")
        return _failure(hashlib.sha256(text.encode()).hexdigest())

    def repair(_context):
        repair.n += 1
        return RepairProposal(
            summary="Change the backend again.",
            files=[RepairPatch(relative_path="backend/main.py", content=f"print({repair.n})\n")],
        )

    repair.n = 0
    state = _start(monkeypatch, tmp_path, tests, repair, run_id="runbudget1")

    assert state["status"] == "failed"
    assert state["repair_attempts"] == 3
    assert "retry budget" in state["errors"][-1]["message"]
    assert len(state["test_results"]) == 4


def test_invalid_patch_does_not_change_the_file(monkeypatch, tmp_path):
    def tests(_workspace, _run_id):
        return _failure("syntax-failure")

    def repair(_context):
        return RepairProposal(
            summary="Introduce a syntax error.",
            files=[RepairPatch(relative_path="backend/main.py", content="def (\n")],
        )

    state = _start(monkeypatch, tmp_path, tests, repair, run_id="runbadpatch")

    assert state["status"] == "failed"
    assert Path(state["workspace_path"], "backend/main.py").read_text(encoding="utf-8") == _BACKEND_SOURCE
    assert state["repair_attempts"] == 0


def test_repair_rejects_paths_outside_the_workspace(monkeypatch, tmp_path):
    def tests(_workspace, _run_id):
        return _failure("path-failure")

    def repair(_context):
        return RepairProposal(
            summary="Escape the workspace.",
            files=[RepairPatch(relative_path="../secret.py", content="print('no')\n")],
        )

    state = _start(monkeypatch, tmp_path, tests, repair, run_id="runescape1")

    assert state["status"] == "failed"
    assert not (tmp_path / "generated" / "secret.py").exists()
    assert not (tmp_path / "secret.py").exists()
    assert "scope" in state["errors"][-1]["message"].lower()


def test_patch_write_failure_rolls_back(tmp_path, monkeypatch):
    workspace = create_run_workspace("rollback01", tmp_path)
    write_text_atomic(workspace, "backend/main.py", "print('one')\n")
    write_text_atomic(workspace, "tests/test_api.py", "def test_api():\n    assert True\n")
    state = {"artifact_manifest": _manifest(), "run_id": "rollback01", "repair_attempts": 0}
    proposal = RepairProposal(
        summary="Update both Python files.",
        files=[
            RepairPatch(relative_path="backend/main.py", content="print('changed')\n"),
            RepairPatch(relative_path="tests/test_api.py", content="def test_api():\n    assert 1 == 1\n"),
        ],
    )
    real = replace_text_atomic
    calls = {"n": 0}

    def wrapped(target, relative, content):
        calls["n"] += 1
        if calls["n"] == 2:
            raise WorkspaceSecurityError("disk full")
        real(target, relative, content)

    monkeypatch.setattr("execution.repair.replace_text_atomic", wrapped)
    with pytest.raises(Exception, match="rolled back"):
        apply_proposal(workspace, proposal, state, attempt=1)

    assert (workspace / "backend" / "main.py").read_text(encoding="utf-8") == "print('one')\n"
    assert "assert True" in (workspace / "tests" / "test_api.py").read_text(encoding="utf-8")


def test_retry_budget_setting_rejects_values_above_three(monkeypatch, tmp_path):
    monkeypatch.setenv("MAX_REPAIR_ATTEMPTS", "4")
    with pytest.raises(ValueError):
        load_max_repair_attempts()
    state = workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated",
        db_path=tmp_path / "team.db",
        run_id="runsetting1",
    )
    assert state["status"] == "failed"
    assert state["errors"][0]["agent"] == "settings"
