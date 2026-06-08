import pytest


class DummyResponse:
    def __init__(self, text=None, blocks=None):
        self.text = text
        self.blocks = blocks or []


class DummyBlock:
    def __init__(self, type, text):
        self.type = type
        self.text = text


class DummyOllamaClient:
    def __init__(self, response):
        self.response = response

    def generate(self, *args, **kwargs):
        return self.response


@pytest.fixture
def monkeypatch_ollama(monkeypatch):
    def _patch(response):
        client = DummyOllamaClient(response)
        monkeypatch.setattr("agents.ollama_client", client)
        return client

    return _patch


def test_product_manager_agent_returns_text(monkeypatch_ollama):
    from agents import product_manager_agent

    response = DummyResponse(text="Product roadmap and milestones")
    monkeypatch_ollama(response)

    result = product_manager_agent("Build a new feature")

    assert isinstance(result, str)
    assert "roadmap" in result.lower()


def test_architect_agent_returns_text(monkeypatch_ollama):
    from agents import architect_agent

    response = DummyResponse(text="Architecture overview with components")
    monkeypatch_ollama(response)

    result = architect_agent("Design the system")

    assert isinstance(result, str)
    assert "architecture" in result.lower()


def test_backend_agent_returns_file_blocks(monkeypatch_ollama):
    from agents import backend_agent

    file_block = DummyBlock(type="file", text="def handler(): pass")
    response = DummyResponse(blocks=[file_block])
    monkeypatch_ollama(response)

    result = backend_agent("Implement API endpoint")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].type == "file"
    assert "def handler" in result[0].text


def test_reviewer_agent_returns_review_output(monkeypatch_ollama):
    from agents import reviewer_agent

    response = DummyResponse(text="Looks good with a few suggested improvements")
    monkeypatch_ollama(response)

    result = reviewer_agent("Review this code")

    assert isinstance(result, str)
    assert "review" in result.lower() or "suggested" in result.lower()
