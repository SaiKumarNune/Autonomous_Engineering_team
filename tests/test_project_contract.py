from pathlib import Path

import pytest

from agents.artifacts import AgentArtifacts, GeneratedFile
from agents.devops_templates import render_fastapi_sqlite
from database.checkpoint import SqliteCheckpointSaver
from execution.dependencies import requirements_text
from execution.project_contract import (
    backend_contract_issues,
    normalize_backend_requirements,
    qa_contract_issues,
    sandbox_dependency_block,
)
from tools.workspace import create_run_workspace, write_text_atomic
from workflows.execution_nodes import execute_tests_node
from workflows.software_team_graph import build_software_team_graph

ROOT = Path(__file__).resolve().parents[1]
SAVED_DB = ROOT / "database" / "phase4_validation.db"
SAVED_PROJECT = ROOT / "generated_projects" / "phase4230243t3"
PINS = requirements_text()
BACKEND = """import os
from fastapi import FastAPI
from sqlalchemy import create_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./tasks.db")
engine = create_engine(DATABASE_URL)
app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True}
"""
TEST = "from fastapi.testclient import TestClient\nfrom backend.main import app\n\ndef test_health():\n    assert TestClient(app).get('/health').status_code == 200\n"


def _files(**contents: str) -> AgentArtifacts:
    return AgentArtifacts(
        summary="contract",
        files=[
            GeneratedFile(
                relative_path=path,
                content=content,
                language="text" if path.endswith(".txt") else "python",
                source_agent="Backend Engineer",
            )
            for path, content in contents.items()
        ],
    )


def test_backend_normalizes_requirements_and_hands_off_to_devops(tmp_path):
    artifacts = _files(**{"backend/main.py": BACKEND, "backend/requirements.txt": PINS})
    normalize_backend_requirements(artifacts)
    assert artifacts.files[1].relative_path == "requirements.txt"
    assert backend_contract_issues(artifacts) == []

    tests = AgentArtifacts(
        summary="tests",
        files=[
            GeneratedFile(
                relative_path="tests/test_api.py",
                content=TEST,
                language="python",
                source_agent="QA Engineer",
            )
        ],
    )
    assert qa_contract_issues(tests, {"backend", "main"}) == []

    workspace = create_run_workspace("runhandoff", tmp_path)
    manifest = []
    for item in [*artifacts.files, *tests.files]:
        write_text_atomic(workspace, item.relative_path, item.content)
        manifest.append({"relative_path": item.relative_path, "source_agent": item.source_agent})
    rendered = render_fastapi_sqlite(str(workspace), manifest)
    assert [item.relative_path for item in rendered.files] == [
        "Dockerfile",
        "docker-compose.yml",
        ".github/workflows/ci.yml",
    ]
    assert "image:" not in rendered.files[1].content
    blocked, _inspection = sandbox_dependency_block(workspace)
    assert blocked is None


def _sandbox_must_not_start(*_args, **_kwargs):
    raise AssertionError("sandbox should not start")


def test_dependency_mismatch_and_missing_requirements_block_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr("execution.service.run_validated_tests", _sandbox_must_not_start)
    mismatch = create_run_workspace("runmismatch", tmp_path)
    write_text_atomic(mismatch, "backend/main.py", BACKEND)
    write_text_atomic(mismatch, "requirements.txt", PINS.replace("fastapi==0.115.12", "fastapi==0.0.1"))
    failed = execute_tests_node({"workspace_path": str(mismatch), "run_id": "runmismatch", "db_path": ""})
    assert failed["status"] == "failed"
    assert "unpinned or mismatched dependency fastapi==0.0.1" in failed["errors"][0]["message"]
    assert "Sandbox execution was not started" in failed["errors"][0]["message"]

    missing = create_run_workspace("runmissing1", tmp_path)
    write_text_atomic(missing, "backend/main.py", BACKEND)
    blocked, _inspection = sandbox_dependency_block(missing)
    assert blocked is not None
    assert "requirements.txt is missing" in blocked


def test_unsupported_import_blocks_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr("execution.service.run_validated_tests", _sandbox_must_not_start)
    workspace = create_run_workspace("runstream1", tmp_path)
    write_text_atomic(workspace, "backend/main.py", BACKEND)
    write_text_atomic(workspace, "frontend/app.py", "import streamlit\n")
    write_text_atomic(workspace, "requirements.txt", PINS)
    failed = execute_tests_node({"workspace_path": str(workspace), "run_id": "runstream1", "db_path": ""})
    assert "unsupported dependency streamlit imported by frontend/app.py" in failed["errors"][0]["message"]
    assert failed["tests_passed"] is False


def test_qa_rejects_a_non_collectable_test():
    artifacts = AgentArtifacts(
        summary="tests",
        files=[
            GeneratedFile(
                relative_path="tests/check.py",
                content="def check():\n    assert True\n",
                language="python",
                source_agent="QA Engineer",
            )
        ],
    )
    issues = qa_contract_issues(artifacts)
    assert any("collectable pytest" in issue for issue in issues)


@pytest.mark.skipif(not SAVED_DB.is_file() or not SAVED_PROJECT.is_dir(), reason="saved project is absent")
def test_saved_checkpoint_is_not_resumed():
    saver = SqliteCheckpointSaver(str(SAVED_DB))
    graph = build_software_team_graph(checkpointer=saver)
    finished = graph.get_state({"configurable": {"thread_id": "phase4230243t3"}})
    interrupted = graph.get_state({"configurable": {"thread_id": "phase4232616t1"}})

    assert finished.next == ()
    assert finished.values["status"] == "failed"
    blocked, _inspection = sandbox_dependency_block(SAVED_PROJECT)
    assert blocked is not None
    assert "streamlit" in blocked
    assert interrupted.next == ("frontend_engineer",)
    assert interrupted.values["status"] == "running"
