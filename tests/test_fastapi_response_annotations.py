from pathlib import Path

import pytest

from agents.artifacts import AgentArtifacts, GeneratedFile
from execution.dependencies import requirements_text
from execution.project_contract import backend_contract_issues
from tools.workspace import create_run_workspace
from workflows.software_team_graph import backend_node

ROOT = Path(__file__).resolve().parents[1]
SAVED_MAIN = ROOT / "generated_projects" / "phase4demo2" / "backend" / "main.py"
PINS = requirements_text()
HEADER = """import os
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./tasks.db")
engine = create_engine(DATABASE_URL)
Base = declarative_base()
app = FastAPI()


class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String)


class TaskCreate(BaseModel):
    title: str


def get_db():
    yield None
"""
ORM_PARAMETER = HEADER + """

@app.post("/tasks")
def create_task(task: Task, db: object = Depends(get_db)):
    return {"title": task.title}
"""
PYDANTIC_RESPONSE = HEADER + """

@app.post("/tasks", response_model=TaskCreate)
def create_task(task: TaskCreate) -> TaskCreate:
    return task
"""
ORM_RETURN = HEADER + """

@app.post("/tasks")
def create_task(task: TaskCreate) -> Task:
    return task
"""
ORM_RESPONSE_MODEL = HEADER + """

@app.get("/tasks", response_model=Task)
def list_tasks():
    return []
"""


def _files(**contents: str) -> AgentArtifacts:
    files = [
        GeneratedFile(
            relative_path=path,
            content=content,
            language="text" if path.endswith(".txt") else "python",
            source_agent="Backend Engineer",
        )
        for path, content in contents.items()
    ]
    files.append(
        GeneratedFile(
            relative_path="requirements.txt",
            content=PINS,
            language="text",
            source_agent="Backend Engineer",
        )
    )
    return AgentArtifacts(summary="backend", files=files)


def test_sqlalchemy_parameter_is_reported_and_pydantic_models_pass():
    invalid = backend_contract_issues(_files(**{"backend/main.py": ORM_PARAMETER}))
    assert invalid == [
        "backend/main.py:28: create_task parameter 'task' uses SQLAlchemy model Task. Use a Pydantic schema."
    ]
    assert backend_contract_issues(_files(**{"backend/main.py": PYDANTIC_RESPONSE})) == []


def test_orm_return_annotation_and_response_model_are_reported():
    returned = backend_contract_issues(_files(**{"backend/main.py": ORM_RETURN}))
    assert any("return annotation uses SQLAlchemy model Task" in issue for issue in returned)
    modeled = backend_contract_issues(_files(**{"backend/main.py": ORM_RESPONSE_MODEL}))
    assert any("response_model uses SQLAlchemy model Task" in issue for issue in modeled)


def test_imported_orm_model_is_reported_without_rewriting_source():
    models = """from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String)
"""
    main = """import os
from fastapi import FastAPI
from sqlalchemy import create_engine

from backend.models import Task

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./tasks.db")
engine = create_engine(DATABASE_URL)
app = FastAPI()


@app.post("/tasks")
def create_task(task: Task):
    return {"id": 1}
"""
    issues = backend_contract_issues(_files(**{"backend/models.py": models, "backend/main.py": main}))
    assert issues == [
        "backend/main.py:13: create_task parameter 'task' uses SQLAlchemy model Task. Use a Pydantic schema."
    ]
    assert "class Task(Base)" in models


def test_annotation_correction_is_attempted_once_and_validated_before_writing(tmp_path, monkeypatch):
    calls = []

    def backend(_architecture, _requirements, correction=""):
        calls.append(correction)
        if not correction:
            return _files(**{"backend/main.py": ORM_PARAMETER})
        return _files(**{"backend/main.py": PYDANTIC_RESPONSE})

    monkeypatch.setattr("agents.llm.ChatOllama", _ollama_must_not_start)
    monkeypatch.setattr("workflows.software_team_graph.run_backend_engineer_agent", backend)
    workspace = create_run_workspace("runannot1", tmp_path)
    update = backend_node(
        {
            "architecture": "FastAPI and SQLite",
            "requirements": "Task list",
            "workspace_path": str(workspace),
            "run_id": "runannot1",
            "artifact_manifest": [],
        }
    )

    assert len(calls) == 2
    assert "SQLAlchemy model Task" in calls[1]
    assert "Pydantic" in calls[1]
    assert update["backend_code"] == "backend"
    written = (workspace / "backend" / "main.py").read_text(encoding="utf-8")
    assert "class TaskCreate(BaseModel)" in written
    assert "def create_task(task: Task," not in written


def test_annotation_correction_that_still_fails_is_not_written(tmp_path, monkeypatch):
    calls = []

    def backend(_architecture, _requirements, correction=""):
        calls.append(correction)
        return _files(**{"backend/main.py": ORM_PARAMETER})

    monkeypatch.setattr("workflows.software_team_graph.run_backend_engineer_agent", backend)
    workspace = create_run_workspace("runannot2", tmp_path)
    update = backend_node(
        {
            "architecture": "FastAPI and SQLite",
            "requirements": "Task list",
            "workspace_path": str(workspace),
            "run_id": "runannot2",
            "artifact_manifest": [],
        }
    )

    assert len(calls) == 2
    assert update["status"] == "failed"
    assert "SQLAlchemy model Task" in update["errors"][0]["message"]
    assert list(workspace.rglob("*.py")) == []


def _ollama_must_not_start(*_args, **_kwargs):
    raise AssertionError("Ollama must not be called")


@pytest.mark.skipif(not SAVED_MAIN.is_file(), reason="phase4demo2 backend is absent")
def test_saved_phase4demo2_annotation_is_classified_without_changing_the_file():
    original = SAVED_MAIN.read_text(encoding="utf-8")
    issues = backend_contract_issues(_files(**{"backend/main.py": original}))
    assert SAVED_MAIN.read_text(encoding="utf-8") == original
    assert any(
        "create_task parameter 'task' uses SQLAlchemy model Task" in issue
        for issue in issues
    )
