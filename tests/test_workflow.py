import importlib
import inspect
import json
import sys
import types

import pytest


EXPECTED_FINAL_STATE = {
    "requirements": "Defined requirements",
    "architecture": "Proposed architecture",
    "backend_code": "Generated backend code",
    "frontend_code": "Generated frontend code",
    "qa_tests": "Created QA test cases",
    "devops_files": "Created devops files",
    "review": "Review summary",
    "status": "completed",
}

SAMPLE_PROMPT = (
    "Design and implement a full stack application with requirements, architecture, "
    "backend code, frontend code, QA tests, devops files, review, and status."
)


class FakeOllamaResponse:
    def __init__(self, payload):
        self.payload = payload
        self.text = json.dumps(payload)
        self.content = json.dumps(payload)

    def json(self):
        return self.payload


class FakeCompletion:
    @staticmethod
    def create(*args, **kwargs):
        return FakeOllamaResponse(EXPECTED_FINAL_STATE)


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def completion(self, *args, **kwargs):
        return FakeOllamaResponse(EXPECTED_FINAL_STATE)

    def create(self, *args, **kwargs):
        return FakeOllamaResponse(EXPECTED_FINAL_STATE)

    def invoke(self, *args, **kwargs):
        return FakeOllamaResponse(EXPECTED_FINAL_STATE)


def _install_fake_ollama_module():
    fake_ollama = types.ModuleType("ollama")
    fake_ollama.Completion = FakeCompletion
    fake_ollama.Client = FakeClient
    fake_ollama.Ollama = FakeClient
    fake_ollama.OllamaClient = FakeClient
    fake_ollama.create = FakeCompletion.create
    sys.modules["ollama"] = fake_ollama


def _import_workflow_module():
    candidates = [
        "workflows.software_team_graph",
    ]

    for module_name in candidates:
        if module_name in sys.modules:
            del sys.modules[module_name]

    for module_name in candidates:
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue

    pytest.skip("Could not import workflows.software_team_graph")


def _invoke_callable(callable_obj):
    signature = inspect.signature(callable_obj)

    if len(signature.parameters) == 0:
        return callable_obj()

    return callable_obj(SAMPLE_PROMPT)


def _resolve_workflow_runner(module):
    if hasattr(module, "run_software_team_workflow"):
        return getattr(module, "run_software_team_workflow")

    if hasattr(module, "LangGraphWorkflow"):
        workflow_class = getattr(module, "LangGraphWorkflow")
        workflow_instance = workflow_class()

        for name in ("run", "execute", "build", "start"):
            if hasattr(workflow_instance, name):
                return getattr(workflow_instance, name)

    if hasattr(module, "Workflow"):
        workflow_class = getattr(module, "Workflow")
        workflow_instance = workflow_class()

        for name in ("run", "execute", "build", "start"):
            if hasattr(workflow_instance, name):
                return getattr(workflow_instance, name)

    for entrypoint in ("run_workflow", "run", "execute", "main"):
        if hasattr(module, entrypoint):
            return getattr(module, entrypoint)

    raise RuntimeError("No workflow entrypoint found in module")


def _normalize_state(state):
    if isinstance(state, dict):
        return state

    if hasattr(state, "state"):
        return getattr(state, "state")

    if hasattr(state, "to_dict"):
        return state.to_dict()

    if hasattr(state, "__dict__"):
        return vars(state)

    return {"result": state}


def test_langgraph_workflow_with_mocked_agent_outputs(monkeypatch):
    _install_fake_ollama_module()

    workflow_module = _import_workflow_module()

    monkeypatch.setattr(
        workflow_module,
        "run_product_manager_agent",
        lambda user_request: EXPECTED_FINAL_STATE["requirements"],
        raising=False,
    )

    monkeypatch.setattr(
        workflow_module,
        "run_architect_agent",
        lambda requirements: EXPECTED_FINAL_STATE["architecture"],
        raising=False,
    )

    monkeypatch.setattr(
        workflow_module,
        "run_backend_engineer_agent",
        lambda architecture, requirements: EXPECTED_FINAL_STATE["backend_code"],
        raising=False,
    )

    monkeypatch.setattr(
        workflow_module,
        "run_frontend_engineer_agent",
        lambda architecture, requirements: EXPECTED_FINAL_STATE["frontend_code"],
        raising=False,
    )

    monkeypatch.setattr(
        workflow_module,
        "run_qa_engineer_agent",
        lambda generated_code, requirements: EXPECTED_FINAL_STATE["qa_tests"],
        raising=False,
    )

    monkeypatch.setattr(
        workflow_module,
        "run_devops_engineer_agent",
        lambda project_files_summary: EXPECTED_FINAL_STATE["devops_files"],
        raising=False,
    )

    monkeypatch.setattr(
        workflow_module,
        "run_reviewer_agent",
        lambda all_outputs: EXPECTED_FINAL_STATE["review"],
        raising=False,
    )

    workflow_runner = _resolve_workflow_runner(workflow_module)
    final_state = _invoke_callable(workflow_runner)
    normalized_state = _normalize_state(final_state)

    for expected_key in EXPECTED_FINAL_STATE:
        assert expected_key in normalized_state

    assert normalized_state["status"] == "completed"