from pathlib import Path

import pytest

from agents.artifacts import AgentArtifacts, GeneratedFile
from agents.backend_engineer_agent import backend_sqlite_repair_request
from database.checkpoint import SqliteCheckpointSaver
from execution.dependencies import requirements_text
from execution.project_contract import backend_contract_issues
from tools.workspace import create_run_workspace
from workflows.software_team_graph import backend_node, build_software_team_graph

ROOT = Path(__file__).resolve().parents[1]
DEMO_DB = ROOT / "database" / "phase4_demo.db"
PINS = requirements_text()
NO_SQLITE = """import os
from fastapi import FastAPI
from sqlalchemy import create_engine

DATABASE_URL = os.environ.get("DATABASE_URL")
engine = create_engine(DATABASE_URL)
app = FastAPI()
"""
WITH_SQLITE = NO_SQLITE.replace(
    'os.environ.get("DATABASE_URL")',
    'os.environ.get("DATABASE_URL", "sqlite:///./tasks.db")',
)
SQLITE3_IMPORT = "import sqlite3\nfrom fastapi import FastAPI\napp = FastAPI()\nsqlite3.connect(':memory:')\n"


def _artifacts(source: str) -> AgentArtifacts:
    return AgentArtifacts(
        summary="backend",
        files=[
            GeneratedFile(
                relative_path="backend/main.py",
                content=source,
                language="python",
                source_agent="Backend Engineer",
            ),
            GeneratedFile(
                relative_path="requirements.txt",
                content=PINS,
                language="text",
                source_agent="Backend Engineer",
            ),
        ],
    )


def test_sqlite_validator_rejects_an_environment_url_without_a_sqlite_literal():
    issues = backend_contract_issues(_artifacts(NO_SQLITE))
    assert issues == ["missing SQLite configuration."]
    assert backend_contract_issues(_artifacts(WITH_SQLITE)) == []
    assert backend_contract_issues(_artifacts(SQLITE3_IMPORT)) == []


def test_missing_sqlite_requests_one_correction_and_writes_only_the_valid_result(tmp_path, monkeypatch):
    calls = []

    def backend(_architecture, _requirements, correction=""):
        calls.append(correction)
        if not correction:
            return _artifacts(NO_SQLITE)
        return _artifacts(WITH_SQLITE)

    monkeypatch.setattr("agents.llm.ChatOllama", _ollama_must_not_start)
    monkeypatch.setattr(
        "workflows.software_team_graph.run_backend_engineer_agent",
        backend,
    )
    workspace = create_run_workspace("runsqlite1", tmp_path)
    update = backend_node(
        {
            "architecture": "FastAPI and SQLite",
            "requirements": "Task management API",
            "workspace_path": str(workspace),
            "run_id": "runsqlite1",
            "artifact_manifest": [],
        }
    )

    assert len(calls) == 2
    assert calls[0] == ""
    assert "missing SQLite configuration." in calls[1]
    assert "PATH: backend/main.py" in calls[1]
    assert "DATABASE_URL = os.environ.get" in calls[1]
    assert update["backend_code"] == "backend"
    written = (workspace / "backend" / "main.py").read_text()
    assert "sqlite:///./tasks.db" in written
    assert 'os.environ.get("DATABASE_URL")\n' not in written


def test_a_second_sqlite_miss_is_not_written(tmp_path, monkeypatch):
    calls = []

    def backend(_architecture, _requirements, correction=""):
        calls.append(correction)
        return _artifacts(NO_SQLITE)

    monkeypatch.setattr(
        "workflows.software_team_graph.run_backend_engineer_agent",
        backend,
    )
    workspace = create_run_workspace("runsqlite2", tmp_path)
    update = backend_node(
        {
            "architecture": "FastAPI and SQLite",
            "requirements": "Task management API",
            "workspace_path": str(workspace),
            "run_id": "runsqlite2",
            "artifact_manifest": [],
        }
    )

    assert len(calls) == 2
    assert update["status"] == "failed"
    assert update["errors"] == [
        {"agent": "Backend Engineer", "message": "missing SQLite configuration."}
    ]
    assert list(workspace.rglob("*.py")) == []


def test_repair_request_includes_the_validation_error_and_original_files():
    request = backend_sqlite_repair_request(
        ["missing SQLite configuration."],
        _artifacts(NO_SQLITE),
    )
    assert "missing SQLite configuration." in request
    assert "PATH: backend/main.py" in request
    assert "PATH: requirements.txt" in request
    assert "sqlite:///./tasks.db" in request


def _ollama_must_not_start(*_args, **_kwargs):
    raise AssertionError("Ollama must not be called")


@pytest.mark.skipif(not DEMO_DB.is_file(), reason="phase4demo1 database is absent")
def test_phase4demo1_checkpoint_is_terminal_and_has_no_backend_files():
    saver = SqliteCheckpointSaver(str(DEMO_DB))
    snapshot = build_software_team_graph(checkpointer=saver).get_state(
        {"configurable": {"thread_id": "phase4demo1"}}
    )
    assert snapshot.next == ()
    assert snapshot.values["status"] == "failed"
    assert snapshot.values["requirements"]
    assert snapshot.values["architecture"]
    assert snapshot.values["backend_code"] == ""
    assert snapshot.values["artifact_manifest"] == []
    assert snapshot.values["errors"] == [
        {"agent": "Backend Engineer", "message": "missing SQLite configuration."}
    ]
