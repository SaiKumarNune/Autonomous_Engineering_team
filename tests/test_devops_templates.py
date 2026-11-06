import ast

import pytest

from agents.devops_engineer_agent import run_devops_engineer_agent
from agents.errors import AgentError
from agents.fast_mode import SIMULATED_LABEL
from execution.dependencies import requirements_text
from tools.devops_validation import dockerfile_report, load_yaml_document
from tools.workspace import create_run_workspace, write_text_atomic

PINS = requirements_text()
APPLICATION = '''
import os

import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./tasks.db")
engine = create_engine(DATABASE_URL)
app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000)
'''
TEST_FILE = "def test_health():\n    assert True\n"


def _project(tmp_path, files: dict[str, str]):
    workspace = create_run_workspace("runtpl001", tmp_path)
    for relative, content in files.items():
        write_text_atomic(workspace, relative, content)
    manifest = [
        {"relative_path": relative, "source_agent": "Backend Engineer", "language": "python"}
        for relative in files
    ]
    return workspace, manifest


def _render(tmp_path, files: dict[str, str]):
    workspace, manifest = _project(tmp_path, files)
    return run_devops_engineer_agent(
        "REQUIREMENTS:\nTask API\n\nARCHITECTURE:\nFastAPI and SQLite\n",
        workspace_path=str(workspace),
        manifest=manifest,
    )


def test_fastapi_sqlite_template_is_consistent(tmp_path, monkeypatch):
    class ExplodingChat:
        def __init__(self, **_kwargs):
            raise AssertionError("model should not be called")

    monkeypatch.setattr("agents.llm.ChatOllama", ExplodingChat)
    artifacts = _render(
        tmp_path,
        {
            "backend/main.py": APPLICATION,
            "tests/test_api.py": TEST_FILE,
            "requirements.txt": PINS,
        },
    )
    files = {item.relative_path: item.content for item in artifacts.files}

    assert list(files) == ["Dockerfile", "docker-compose.yml", ".github/workflows/ci.yml"]
    assert "backend.main:app" in files["Dockerfile"]
    assert '"9000"' in files["Dockerfile"]
    assert 'ENV DATABASE_URL="sqlite:///./tasks.db"' in files["Dockerfile"]
    assert "fastapi==0.115.12" in artifacts.summary
    assert "uvicorn==0.34.0" in artifacts.summary
    assert "Port 9000 comes from the application" in artifacts.summary
    assert "not written by a model" in artifacts.summary
    assert "No SQLite server container" in artifacts.summary
    issues, runtime = dockerfile_report(files["Dockerfile"])
    assert issues == []
    assert runtime == [
        "python",
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "9000",
    ]
    compose = load_yaml_document(files["docker-compose.yml"])
    assert "image" not in compose["services"]["app"]
    assert compose["services"]["app"]["environment"]["DATABASE_URL"] == "sqlite:///./tasks.db"
    assert compose["services"]["app"]["ports"] == ["9000:9000"]
    workflow = load_yaml_document(files[".github/workflows/ci.yml"])
    assert "push" in workflow["on"]
    assert "pull_request" in workflow["on"]
    assert workflow["jobs"]["test"]["steps"][-1]["run"] == "pytest"
    ast.parse(APPLICATION)


def test_unsupported_framework_is_rejected(tmp_path):
    with pytest.raises(AgentError, match="unsupported framework Flask"):
        _render(
            tmp_path,
            {
                "backend/main.py": "from flask import Flask\napp = Flask(__name__)\n",
                "tests/test_api.py": TEST_FILE,
                "requirements.txt": PINS,
            },
        )


def test_missing_entrypoint_is_rejected(tmp_path):
    source = "from fastapi import FastAPI\n\ndef build():\n    return FastAPI()\n"
    with pytest.raises(AgentError, match="missing FastAPI entrypoint"):
        _render(
            tmp_path,
            {
                "backend/main.py": source,
                "tests/test_api.py": TEST_FILE,
                "requirements.txt": PINS,
            },
        )


def test_ambiguous_entrypoints_are_rejected(tmp_path):
    with pytest.raises(AgentError, match="ambiguous FastAPI entrypoints"):
        _render(
            tmp_path,
            {
                "backend/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
                "backend/admin.py": "from fastapi import FastAPI\napi = FastAPI()\n",
                "tests/test_api.py": TEST_FILE,
                "requirements.txt": PINS,
            },
        )


def test_mismatched_dependency_pin_is_rejected(tmp_path):
    pins = PINS.replace("fastapi==0.115.12", "fastapi==0.0.1")
    with pytest.raises(AgentError, match="Inconsistent dependency declarations"):
        _render(
            tmp_path,
            {
                "backend/main.py": APPLICATION,
                "tests/test_api.py": TEST_FILE,
                "requirements.txt": pins,
            },
        )


def test_missing_imported_dependency_is_rejected(tmp_path):
    pins = "\n".join(
        line for line in PINS.splitlines() if not line.startswith("sqlalchemy==")
    ) + "\n"
    with pytest.raises(AgentError, match="does not pin sqlalchemy==2.0.40"):
        _render(
            tmp_path,
            {
                "backend/main.py": APPLICATION,
                "tests/test_api.py": TEST_FILE,
                "requirements.txt": pins,
            },
        )


def test_fast_dev_mode_stays_simulated(monkeypatch):
    monkeypatch.setenv("FAST_DEV_MODE", "1")
    artifacts = run_devops_engineer_agent("REQUIREMENTS:\nTask API\n")
    assert artifacts.summary.startswith(SIMULATED_LABEL)
    assert "SIMULATED" in artifacts.files[0].content
