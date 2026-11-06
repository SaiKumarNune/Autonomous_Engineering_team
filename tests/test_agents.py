import json

import pytest
from langchain_core.messages import SystemMessage

from agents.architect_agent import run_architect_agent
from agents.artifacts import AgentArtifacts
from agents.backend_engineer_agent import run_backend_engineer_agent
from agents.devops_engineer_agent import run_devops_engineer_agent
from agents.errors import AgentError
from agents.frontend_engineer_agent import run_frontend_engineer_agent
from agents.llm import get_chat_model
from agents.product_manager_agent import run_product_manager_agent
from agents.prompt_loader import load_prompt
from agents.qa_engineer_agent import run_qa_engineer_agent
from agents.reviewer_agent import run_reviewer_agent

MODEL_TEXT = "Requirements for the tracker"

TEXT_AGENTS = [
    ("Product Manager", run_product_manager_agent, ("Build a tracker",)),
    ("Architect", run_architect_agent, ("Requirements text",)),
    ("Reviewer", run_reviewer_agent, ("All outputs",)),
]

ARTIFACT_AGENTS = [
    ("Backend Engineer", run_backend_engineer_agent, ("Architecture", "Requirements"), "backend/main.py", "python", "print('ok')\n"),
    ("Frontend Engineer", run_frontend_engineer_agent, ("Architecture", "Requirements", "backend/main.py"), "frontend/app.py", "python", "print('ok')\n"),
    ("QA Engineer", run_qa_engineer_agent, ("generated files", "Requirements"), "tests/test_api.py", "python", "def test_ok():\n    assert True\n"),
]

AGENT_CALLS = TEXT_AGENTS + [
    (name, runner, args) for name, runner, args, *_rest in ARTIFACT_AGENTS
]


def _artifact_json(agent, path, language, content):
    return json.dumps(
        {
            "summary": "generated",
            "files": [
                {
                    "relative_path": path,
                    "content": content,
                    "language": language,
                    "source_agent": agent,
                }
            ],
        }
    )


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeChat:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def invoke(self, messages):
        FakeChat.messages = messages
        FakeChat.kwargs = self.kwargs
        if FakeChat.error is not None:
            raise FakeChat.error
        return FakeResponse(FakeChat.content)


def _patch_chat(monkeypatch, content=MODEL_TEXT, error=None):
    FakeChat.content = content
    FakeChat.error = error
    FakeChat.messages = None
    FakeChat.kwargs = None
    monkeypatch.setattr("agents.llm.ChatOllama", FakeChat)


def test_load_prompt_uses_repository_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    text = load_prompt("frontend_prompt.txt")
    assert "Frontend Engineer" in text


def test_load_prompt_rejects_path_escape():
    with pytest.raises(AgentError, match="file name"):
        load_prompt("../secrets.txt")


@pytest.mark.parametrize("agent_name,runner,args", TEXT_AGENTS)
def test_agents_return_model_text(monkeypatch, agent_name, runner, args):
    _patch_chat(monkeypatch)
    monkeypatch.setenv("OLLAMA_MODEL", "unit-test-model")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0")

    assert runner(*args) == MODEL_TEXT
    assert FakeChat.kwargs["model"] == "unit-test-model"
    assert FakeChat.kwargs["base_url"] == "http://127.0.0.1:11434"
    assert FakeChat.kwargs["temperature"] == 0.0
    assert any(isinstance(message, SystemMessage) and message.content for message in FakeChat.messages)


@pytest.mark.parametrize(
    "agent_name,runner,args,path,language,content",
    ARTIFACT_AGENTS,
)
def test_artifact_agents_parse_structured_json(
    monkeypatch, agent_name, runner, args, path, language, content
):
    _patch_chat(monkeypatch, content=_artifact_json(agent_name, path, language, content))

    result = runner(*args)

    assert isinstance(result, AgentArtifacts)
    assert result.files[0].relative_path == path
    assert result.files[0].source_agent == agent_name
    assert "JSON schema" in FakeChat.messages[0].content


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "{",
        '{"summary": "missing files"}',
        '{"summary": "x", "files": []}',
    ],
)
def test_invalid_artifact_responses_are_rejected(monkeypatch, payload):
    _patch_chat(monkeypatch, content=payload)

    with pytest.raises(AgentError, match="JSON|validation|empty"):
        run_backend_engineer_agent("Architecture", "Requirements")


def test_structured_output_parser_error_is_not_accepted(monkeypatch):
    class StructuredChat:
        def __init__(self, **_kwargs):
            pass

        def with_structured_output(self, schema, **_kwargs):
            class Runnable:
                def invoke(self, _messages):
                    return {
                        "raw": FakeResponse("this is not json"),
                        "parsed": None,
                        "parsing_error": ValueError("schema mismatch"),
                    }

            return Runnable()

        def invoke(self, _messages):
            raise AssertionError("plain invoke should not run before the structured parser")

    monkeypatch.setattr("agents.llm.ChatOllama", StructuredChat)

    with pytest.raises(AgentError, match="not JSON|schema mismatch"):
        run_backend_engineer_agent("Architecture", "Requirements")


@pytest.mark.parametrize("agent_name,runner,args", AGENT_CALLS)
def test_agents_raise_on_model_failure(monkeypatch, agent_name, runner, args):
    _patch_chat(monkeypatch, error=RuntimeError("connection refused"))

    with pytest.raises(AgentError) as exc_info:
        runner(*args)

    assert exc_info.value.agent_name == agent_name
    assert "connection refused" in exc_info.value.message


def test_empty_model_response_is_an_agent_error(monkeypatch):
    _patch_chat(monkeypatch, content="  ")

    with pytest.raises(AgentError, match="empty response"):
        run_product_manager_agent("Build a tracker")


@pytest.mark.parametrize(
    "runner,args",
    [
        (run_product_manager_agent, ("  ",)),
        (run_architect_agent, ("",)),
        (run_backend_engineer_agent, ("", "requirements")),
        (run_frontend_engineer_agent, ("architecture", "requirements", " ")),
        (run_qa_engineer_agent, ("", "requirements")),
        (run_devops_engineer_agent, ("  ",)),
        (run_reviewer_agent, ("",)),
    ],
)
def test_agents_reject_empty_input(monkeypatch, runner, args):
    _patch_chat(monkeypatch, error=AssertionError("model should not be called"))

    with pytest.raises(AgentError, match="cannot be empty"):
        runner(*args)


def test_frontend_rejects_free_form_text(monkeypatch):
    _patch_chat(monkeypatch, content="st.title('Tasks')")

    with pytest.raises(AgentError, match="not JSON"):
        run_frontend_engineer_agent("Architecture", "Requirements", "backend/main.py")


def test_devops_agent_does_not_ask_the_model_to_write_files(monkeypatch):
    monkeypatch.setattr(
        "agents.llm.ChatOllama",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("model should not be called")),
    )

    with pytest.raises(AgentError, match="No supported DevOps template"):
        run_devops_engineer_agent("UNIQUE_PROJECT_CONTEXT_42")


def test_chat_model_rejects_invalid_temperature(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "unit-test-model")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "hot")

    def fail_if_called(**_kwargs):
        raise AssertionError("ChatOllama should not be constructed")

    monkeypatch.setattr("agents.llm.ChatOllama", fail_if_called)

    with pytest.raises(AgentError, match="OLLAMA_TEMPERATURE"):
        get_chat_model()


def test_chat_model_omits_base_url_when_unset(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "unit-test-model")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.2")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_NUM_PREDICT", raising=False)
    captured = {}

    class CapturingChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("agents.llm.ChatOllama", CapturingChat)

    get_chat_model()

    assert captured["model"] == "unit-test-model"
    assert captured["temperature"] == 0.2
    assert "base_url" not in captured
    assert captured["num_predict"] == 1024
    assert captured["sync_client_kwargs"]["timeout"] == 240.0
    assert captured["async_client_kwargs"]["timeout"] == 240.0
