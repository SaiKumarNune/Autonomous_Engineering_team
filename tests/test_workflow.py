import sqlite3
from pathlib import Path

import workflows.software_team_graph as workflow
from agents.artifacts import AgentArtifacts, GeneratedFile
from agents.errors import AgentError
from execution.dependencies import requirements_text
from tools.workspace import WorkspaceSecurityError
from workflows.software_team_graph import OUTPUT_SECTIONS, SoftwareTeamState


class _Calls(list):
    def __init__(self):
        super().__init__()
        self.arguments: dict[str, tuple] = {}


def _artifacts(agent: str, path: str, summary: str, content: str, language: str) -> AgentArtifacts:
    return AgentArtifacts(
        files=[
            GeneratedFile(
                relative_path=path,
                content=content,
                language=language,
                source_agent=agent,
            )
        ],
        summary=summary,
    )


_BACKEND_SOURCE = """import os
from fastapi import FastAPI
from sqlalchemy import create_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./tasks.db")
engine = create_engine(DATABASE_URL)
app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True}
"""


def _backend_artifacts(content: str = _BACKEND_SOURCE) -> AgentArtifacts:
    return AgentArtifacts(
        summary="BACK",
        files=[
            GeneratedFile(
                relative_path="backend/main.py",
                content=content,
                language="python",
                source_agent="Backend Engineer",
            ),
            GeneratedFile(
                relative_path="requirements.txt",
                content=requirements_text(),
                language="text",
                source_agent="Backend Engineer",
            ),
        ],
    )


def _patch_agents(monkeypatch, calls, *, fail_at=None, fail_with=None, backend_content=_BACKEND_SOURCE):
    outputs = {
        "product_manager": "REQ",
        "architect": "ARCH",
        "backend": _backend_artifacts(backend_content),
        "frontend": _artifacts(
            "Frontend Engineer",
            "frontend/app.py",
            "FRONT",
            "print('ui')\n",
            "python",
        ),
        "qa": _artifacts(
            "QA Engineer",
            "tests/test_api.py",
            "TESTS",
            "def test_api():\n    assert True\n",
            "python",
        ),
        "devops": _devops_artifacts(),
        "reviewer": "REVIEW",
    }

    def make(name):
        def _fn(*args, **kwargs):
            calls.append(name)
            calls.arguments[name] = args
            if fail_at == name:
                raise fail_with or AgentError(name, "boom")
            return outputs[name]

        return _fn

    calls.arguments = {}
    monkeypatch.setattr(workflow, "run_product_manager_agent", make("product_manager"))
    monkeypatch.setattr(workflow, "run_architect_agent", make("architect"))
    monkeypatch.setattr(workflow, "run_backend_engineer_agent", make("backend"))
    monkeypatch.setattr(workflow, "run_frontend_engineer_agent", make("frontend"))
    monkeypatch.setattr(workflow, "run_qa_engineer_agent", make("qa"))
    monkeypatch.setattr(workflow, "run_devops_engineer_agent", make("devops"))
    monkeypatch.setattr(workflow, "run_reviewer_agent", make("reviewer"))


def _devops_artifacts() -> AgentArtifacts:
    runtime = 'CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
    return AgentArtifacts(
        summary="DEVOPS",
        files=[
            GeneratedFile(
                relative_path="Dockerfile",
                content=f"FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\n{runtime}",
                language="dockerfile",
                source_agent="DevOps Engineer",
            ),
            GeneratedFile(
                relative_path="docker-compose.yml",
                content=(
                    "services:\n"
                    "  app:\n"
                    "    build: .\n"
                    "    ports:\n"
                    '      - "8000:8000"\n'
                    "    environment:\n"
                    "      DATABASE_URL: sqlite:///database.db\n"
                ),
                language="yaml",
                source_agent="DevOps Engineer",
            ),
            GeneratedFile(
                relative_path=".github/workflows/ci.yml",
                content=(
                    "name: CI\n"
                    "on:\n"
                    "  push:\n"
                    "    branches: [main]\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - run: pytest\n"
                ),
                language="yaml",
                source_agent="DevOps Engineer",
            ),
        ],
    )


def _pass_tests(_workspace_path, _run_id):
    return {
        "kind": "passed",
        "command": ["pytest", "-q", "--tb=line", "--disable-warnings", "-p", "no:cacheprovider"],
        "exit_code": 0,
        "stdout": "1 passed",
        "stderr": "",
        "duration_ms": 1,
        "sandbox_enforced": True,
        "failure_signature": "",
        "diagnostics": [],
        "message": "tests passed",
    }


def _run(monkeypatch, tmp_path, calls=None, **kwargs):
    if calls is None:
        calls = _Calls()
    _patch_agents(monkeypatch, calls, **kwargs)
    monkeypatch.setattr("execution.service.run_validated_tests", _pass_tests)

    def _no_repair(*_args, **_kwargs):
        raise AssertionError("repair should not run when sandbox tests pass")

    monkeypatch.setattr("execution.repair.request_repair_proposal", _no_repair)
    final_state = workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated",
        db_path=tmp_path / "team.db",
        run_id="runtest01",
    )
    return calls, final_state


def test_output_sections_match_workflow_state():
    state_fields = set(SoftwareTeamState.__annotations__)
    keys = [key for _label, key in OUTPUT_SECTIONS]
    assert keys == [
        "requirements",
        "architecture",
        "backend_code",
        "frontend_code",
        "qa_tests",
        "devops_files",
        "review",
    ]
    assert set(keys) <= state_fields


def test_langgraph_workflow_with_mocked_agent_outputs(monkeypatch, tmp_path):
    calls, final_state = _run(monkeypatch, tmp_path)

    assert calls == [
        "product_manager",
        "architect",
        "backend",
        "frontend",
        "qa",
        "devops",
        "reviewer",
    ]
    assert final_state["status"] == "awaiting_approval"
    assert final_state["lifecycle_status"] == "awaiting_approval"
    assert final_state["tests_passed"] is True
    assert final_state["errors"] == []
    assert final_state["run_id"] == "runtest01"
    assert final_state["requirements"] == "REQ"
    assert final_state["architecture"] == "ARCH"
    assert final_state["backend_code"] == "BACK"
    assert final_state["frontend_code"] == "FRONT"
    assert final_state["qa_tests"] == "TESTS"
    assert final_state["devops_files"] == "DEVOPS"
    assert final_state["review"] == "REVIEW"
    assert [item["relative_path"] for item in final_state["artifact_manifest"]] == [
        "backend/main.py",
        "requirements.txt",
        "frontend/app.py",
        "tests/test_api.py",
        "Dockerfile",
        "docker-compose.yml",
        ".github/workflows/ci.yml",
    ]
    workspace = Path(final_state["workspace_path"])
    assert (workspace / "backend" / "main.py").read_text(encoding="utf-8") == _BACKEND_SOURCE
    assert (workspace / "requirements.txt").read_text(encoding="utf-8") == requirements_text()
    with sqlite3.connect(tmp_path / "team.db") as connection:
        run_row = connection.execute(
            "SELECT run_id, status FROM workflow_runs"
        ).fetchone()
        stored_files = {
            row[0]
            for row in connection.execute("SELECT file_path FROM generated_files")
        }
        validation_count = connection.execute(
            "SELECT COUNT(*) FROM evaluation_results"
        ).fetchone()[0]
        test_row = connection.execute(
            "SELECT test_type, status, passed_count FROM test_results"
        ).fetchone()
    assert run_row == ("runtest01", "awaiting_approval")
    assert "backend/main.py" in stored_files
    assert validation_count >= 1
    assert test_row == ("passed", "passed", 1)


def test_state_propagates_to_downstream_agents(monkeypatch, tmp_path):
    calls, _final_state = _run(monkeypatch, tmp_path)
    arguments = calls.arguments

    assert arguments["product_manager"] == ("Build a task manager",)
    assert arguments["architect"] == ("REQ",)
    assert arguments["backend"] == ("ARCH", "REQ")
    backend_context = arguments["frontend"][2]
    assert "backend/main.py" in backend_context
    assert "FastAPI" in backend_context
    generated_code, requirements = arguments["qa"]
    assert "backend/main.py" in generated_code
    assert "frontend/app.py" in generated_code
    assert "print('ui')" in generated_code
    assert requirements == "REQ"
    devops_context = arguments["devops"][0]
    assert "ARTIFACT MANIFEST" in devops_context
    assert "tests/test_api.py" in devops_context
    review_input = arguments["reviewer"][0]
    assert "ARTIFACT MANIFEST" in review_input
    assert "VALIDATION RESULTS" in review_input
    assert "DEVOPS" in review_input
    assert "SANDBOX TEST RESULTS" in review_input


def test_agent_failure_stops_the_workflow(monkeypatch, tmp_path):
    calls, final_state = _run(
        monkeypatch,
        tmp_path,
        fail_at="backend",
        fail_with=AgentError("Backend Engineer", "model unavailable"),
    )

    assert calls == ["product_manager", "architect", "backend"]
    assert final_state["status"] == "failed"
    assert final_state["requirements"] == "REQ"
    assert final_state["architecture"] == "ARCH"
    assert final_state["backend_code"] == ""
    assert final_state["frontend_code"] == ""
    assert final_state["review"] == ""
    assert final_state["artifact_manifest"] == []
    assert final_state["errors"] == [
        {"agent": "Backend Engineer", "message": "model unavailable"}
    ]


def test_invalid_python_is_not_written(monkeypatch, tmp_path):
    calls, final_state = _run(monkeypatch, tmp_path, backend_content="def (\n")

    assert calls == ["product_manager", "architect", "backend"]
    assert final_state["status"] == "failed"
    assert final_state["artifact_manifest"] == []
    assert any(item["blocking"] and not item["valid"] for item in final_state["validation_results"])
    assert "syntax" in final_state["errors"][0]["message"].lower()
    workspace = Path(final_state["workspace_path"])
    assert not (workspace / "backend" / "main.py").exists()


def _frontend_workflow(monkeypatch, tmp_path, frontend):
    calls = _Calls()
    _patch_agents(monkeypatch, calls)
    monkeypatch.setattr(workflow, "run_frontend_engineer_agent", frontend)
    monkeypatch.setattr("execution.service.run_validated_tests", _pass_tests)

    def _no_repair(*_args, **_kwargs):
        raise AssertionError("repair should not run when sandbox tests pass")

    monkeypatch.setattr("execution.repair.request_repair_proposal", _no_repair)
    return workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated",
        db_path=tmp_path / "team.db",
        run_id="runfront01",
    )


def test_frontend_syntax_repair_writes_only_the_corrected_file(monkeypatch, tmp_path):
    attempts = {"n": 0}

    def frontend(_architecture, _requirements, _backend, correction="", **_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            assert correction == ""
            return _artifacts(
                "Frontend Engineer",
                "frontend/app.py",
                "FRONT",
                "if True:\n",
                "python",
            )
        assert "SyntaxError:" in correction
        assert "if True:" in correction
        assert "expected an indented block" in correction
        return _artifacts(
            "Frontend Engineer",
            "frontend/app.py",
            "FRONT",
            "value = 'ok'\n",
            "python",
        )

    final_state = _frontend_workflow(monkeypatch, tmp_path, frontend)
    written = Path(final_state["workspace_path"]) / "frontend" / "app.py"

    assert attempts["n"] == 2
    assert final_state["status"] == "awaiting_approval"
    assert written.read_text(encoding="utf-8") == "value = 'ok'\n"
    assert "if True:" not in written.read_text(encoding="utf-8")


def test_frontend_syntax_repair_failure_does_not_write_python(monkeypatch, tmp_path):
    attempts = {"n": 0}

    def frontend(_architecture, _requirements, _backend, correction="", **_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 2:
            assert "Invalid code:" in correction
            assert "if True:" in correction
        return _artifacts(
            "Frontend Engineer",
            "frontend/app.py",
            "FRONT",
            "if True:\n",
            "python",
        )

    final_state = _frontend_workflow(monkeypatch, tmp_path, frontend)
    workspace = Path(final_state["workspace_path"])

    assert attempts["n"] == 2
    assert final_state["status"] == "failed"
    assert "Frontend syntax repair failed" in final_state["errors"][0]["message"]
    assert "Invalid Python was not written" in final_state["errors"][0]["message"]
    assert not (workspace / "frontend" / "app.py").exists()


def test_failed_write_stops_the_workflow(monkeypatch, tmp_path):
    def explode(*_args, **_kwargs):
        raise WorkspaceSecurityError("disk full")

    monkeypatch.setattr("workflows.artifact_pipeline.write_text_atomic", explode)
    calls, final_state = _run(monkeypatch, tmp_path)

    assert "frontend" not in calls
    assert final_state["status"] == "failed"
    assert final_state["artifact_manifest"] == []
    assert "disk full" in final_state["errors"][0]["message"]


def test_reviewer_failure_does_not_complete_the_workflow(monkeypatch, tmp_path):
    calls, final_state = _run(
        monkeypatch,
        tmp_path,
        fail_at="reviewer",
        fail_with=AgentError("Reviewer", "empty response"),
    )

    assert "reviewer" in calls
    assert final_state["status"] == "failed"
    assert final_state["devops_files"] == "DEVOPS"
    assert final_state["review"] == ""
    assert final_state["errors"] == [{"agent": "Reviewer", "message": "empty response"}]
    assert any(item["relative_path"] == "Dockerfile" for item in final_state["artifact_manifest"])


def test_unexpected_agent_exception_fails_the_workflow(monkeypatch, tmp_path):
    calls, final_state = _run(
        monkeypatch,
        tmp_path,
        fail_at="qa",
        fail_with=RuntimeError("socket down"),
    )

    assert final_state["status"] == "failed"
    assert "devops" not in calls
    assert final_state["errors"] == [
        {"agent": "workflow", "message": "RuntimeError: socket down"}
    ]


def test_blank_request_fails_before_completion(monkeypatch, tmp_path):
    calls = _Calls()
    _patch_agents(monkeypatch, calls)
    final_state = workflow.run_software_team_workflow(
        "   ",
        workspace_root=tmp_path / "generated",
        db_path=tmp_path / "team.db",
        run_id="runtest02",
    )

    assert calls == []
    assert final_state["status"] == "failed"
    assert final_state["requirements"] == ""
    assert final_state["errors"][0]["agent"] == "Product Manager"
    assert "user_request" in final_state["errors"][0]["message"]
