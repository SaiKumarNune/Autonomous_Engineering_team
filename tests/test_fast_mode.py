from pathlib import Path

import pytest

import workflows.software_team_graph as workflow
from agents.errors import AgentError
from agents.fast_mode import SIMULATED_LABEL
from agents.frontend_engineer_agent import run_frontend_engineer_agent
from agents.llm import get_chat_model, invoke_agent, invoke_structured_agent
from execution.settings import load_model_num_predict, load_model_timeout_seconds
from tests.test_workflow import _pass_tests
from tools.workspace import create_run_workspace, write_text_atomic
from workflows.artifact_pipeline import deployment_contract, render_stored_files


def test_output_token_limit_is_per_agent_and_configurable(monkeypatch):
    monkeypatch.delenv("OLLAMA_NUM_PREDICT", raising=False)
    monkeypatch.delenv("OLLAMA_NUM_PREDICT_BACKEND_ENGINEER", raising=False)
    monkeypatch.delenv("OLLAMA_TIMEOUT_SECONDS_QA_ENGINEER", raising=False)

    assert load_model_num_predict("Product Manager") == 768
    assert load_model_num_predict("Backend Engineer") == 2048
    assert load_model_num_predict("DevOps Engineer") == 1280
    assert load_model_num_predict() == 1024

    monkeypatch.setenv("OLLAMA_NUM_PREDICT_BACKEND_ENGINEER", "512")
    assert load_model_num_predict("Backend Engineer") == 512
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS_QA_ENGINEER", "30")
    assert load_model_timeout_seconds("QA Engineer") == 30.0
    assert load_model_timeout_seconds("Reviewer") == 240.0


def test_fast_dev_mode_does_not_call_the_model_and_is_labeled(monkeypatch):
    monkeypatch.setenv("FAST_DEV_MODE", "1")

    def fail_if_called(**_kwargs):
        raise AssertionError("ChatOllama should not be constructed")

    monkeypatch.setattr("agents.llm.ChatOllama", fail_if_called)
    text = invoke_agent("Product Manager", "system", "Build a task manager")
    artifacts = invoke_structured_agent("Frontend Engineer", "system", "Build a UI")

    assert SIMULATED_LABEL in text
    assert "Not real model output" in text
    assert artifacts.summary.startswith(SIMULATED_LABEL)
    assert artifacts.files == []
    assert "No frontend files were generated" in artifacts.summary
    assert "Not real model output" in artifacts.summary


def test_fast_dev_mode_workflow_is_simulated(monkeypatch, tmp_path):
    monkeypatch.setenv("FAST_DEV_MODE", "1")
    monkeypatch.setattr("execution.service.run_validated_tests", _pass_tests)

    def _no_repair(*_args, **_kwargs):
        raise AssertionError("repair should not run when sandbox tests pass")

    monkeypatch.setattr("execution.repair.request_repair_proposal", _no_repair)
    state = workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated",
        db_path=tmp_path / "team.db",
        run_id="runfast001",
    )

    assert state["status"] == "awaiting_approval"
    assert state["execution_mode"] == "simulated"
    assert SIMULATED_LABEL in state["requirements"]
    assert "No frontend files were generated" in state["frontend_code"]
    workspace = Path(state["workspace_path"])
    assert not (workspace / "frontend" / "app.py").exists()
    qa_test = (workspace / "tests" / "test_api.py").read_text(encoding="utf-8")
    assert "streamlit" not in qa_test.lower()
    assert "SIMULATED FAST_DEV_MODE" in qa_test
    assert state["run_metrics"]["token_usage"]["reported"] is False


def test_backend_only_frontend_returns_no_files(monkeypatch):
    def fail_if_called(**_kwargs):
        raise AssertionError("ChatOllama should not be constructed")

    monkeypatch.setattr("agents.llm.ChatOllama", fail_if_called)
    artifacts = run_frontend_engineer_agent(
        "One service",
        "FastAPI SQLite API",
        "from fastapi import FastAPI\napp = FastAPI()\n",
    )

    assert artifacts.files == []
    assert "No frontend files were generated" in artifacts.summary
    assert "Not real model output" not in artifacts.summary


@pytest.mark.parametrize(
    "request_text",
    [
        "No frontend UI. No Streamlit. Build a backend-only FastAPI API.",
        "Build the API with no frontend.",
        "Build the API with no UI.",
        "Build the API without a frontend.",
        "Persist tasks with SQLite and use no Streamlit.",
    ],
)
def test_negated_frontend_instructions_skip_the_model(monkeypatch, request_text):
    monkeypatch.setattr("agents.llm.ChatOllama", _ollama_must_not_start)
    artifacts = run_frontend_engineer_agent(
        "Frontend design\nUse Streamlit for the interface.",
        "Requirements mention a frontend and Streamlit.",
        "from fastapi import FastAPI\napp = FastAPI()\n",
        user_request=request_text,
    )

    assert artifacts.files == []
    assert "No frontend files were generated" in artifacts.summary


@pytest.mark.parametrize(
    "request_text",
    [
        "Build a Streamlit dashboard",
        "Create a React interface",
    ],
)
def test_genuine_interface_requests_call_the_frontend_model(monkeypatch, request_text):
    monkeypatch.setattr("agents.llm.get_chat_model", _model_stopped)

    with pytest.raises(AgentError, match="model stopped for the test"):
        run_frontend_engineer_agent(
            "One service",
            "FastAPI SQLite API",
            "from fastapi import FastAPI\napp = FastAPI()\n",
            user_request=request_text,
        )


def test_mixed_intent_follows_explicit_refusal_or_remaining_interface(monkeypatch):
    monkeypatch.setattr("agents.llm.ChatOllama", _ollama_must_not_start)
    refused = run_frontend_engineer_agent(
        "One service",
        "FastAPI SQLite API",
        "from fastapi import FastAPI\napp = FastAPI()\n",
        user_request="Build a Streamlit dashboard. No frontend UI.",
    )
    assert refused.files == []

    monkeypatch.setattr("agents.llm.get_chat_model", _model_stopped)
    with pytest.raises(AgentError, match="model stopped for the test"):
        run_frontend_engineer_agent(
            "One service",
            "FastAPI SQLite API",
            "from fastapi import FastAPI\napp = FastAPI()\n",
            user_request="No Streamlit. Create a React interface.",
        )


def _ollama_must_not_start(*_args, **_kwargs):
    raise AssertionError("Ollama must not be called")


def _model_stopped(_agent_name=None):
    raise AgentError("Frontend Engineer", "model stopped for the test")


def test_user_interface_request_still_calls_the_frontend_model(monkeypatch):
    def fail_model(_agent_name=None):
        raise AgentError("Frontend Engineer", "model stopped for the test")

    monkeypatch.setattr("agents.llm.get_chat_model", fail_model)

    with pytest.raises(AgentError, match="model stopped for the test"):
        run_frontend_engineer_agent(
            "Streamlit UI",
            "Build a frontend",
            "from fastapi import FastAPI\napp = FastAPI()\n",
        )


def test_deployment_contract_keeps_routes_past_the_excerpt_limit(tmp_path):
    workspace = create_run_workspace("runcontract", tmp_path)
    source = (
        "import os\n"
        "DATABASE_URL = os.environ.get('DATABASE_URL')\n"
        "app = None\n"
        + ("# pad\n" * 400)
        + "@app.get('/health')\n"
        + "def health():\n    return 'ok'\n"
        + "@app.delete('/records/{id}')\n"
        + "def delete_record():\n    return 'gone'\n"
        + "uvicorn.run('main:app')\n"
    )
    write_text_atomic(workspace, "backend/main.py", source)
    manifest = [
        {"relative_path": "backend/main.py", "source_agent": "Backend Engineer", "language": "python"}
    ]
    contract = deployment_contract(str(workspace), manifest)
    excerpt = render_stored_files(str(workspace), manifest, {"Backend Engineer"})

    assert "GET /health" in contract
    assert "DELETE /records/{id}" in contract
    assert "DATABASE_URL" in contract
    assert "uvicorn" in contract
    assert "DELETE /records/{id}" not in excerpt


def test_downstream_context_keeps_paths_and_truncates_long_files(tmp_path):
    workspace = create_run_workspace("runclip001", tmp_path)
    write_text_atomic(workspace, "backend/main.py", "print('ok')\n" + ("x" * 5000))
    rendered = render_stored_files(
        str(workspace),
        [{"relative_path": "backend/main.py", "source_agent": "Backend Engineer", "language": "python"}],
        {"Backend Engineer"},
    )

    assert "backend/main.py" in rendered
    assert "print('ok')" in rendered
    assert "[truncated for context]" in rendered
    assert len(rendered) < 5000


def test_chat_model_uses_the_agent_output_limit(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "unit-test-model")
    monkeypatch.delenv("OLLAMA_NUM_PREDICT", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    captured = {}

    class CapturingChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("agents.llm.ChatOllama", CapturingChat)
    get_chat_model("DevOps Engineer")

    assert captured["num_predict"] == 1280
    assert captured["sync_client_kwargs"]["timeout"] == 240.0
