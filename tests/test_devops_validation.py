import json
from pathlib import Path

import yaml

from agents.artifacts import AgentArtifacts
from agents.errors import ArtifactValidationError
from tools.devops_validation import load_yaml_document
from tools.validators import validate_agent_artifacts
from tools.workspace import create_run_workspace
from workflows.artifact_pipeline import store_validated_artifacts

FIXTURE = Path(__file__).parent / "fixtures" / "saved_devops_response.json"
VALID_DOCKERFILE = (
    "FROM python:3.12-slim\n"
    "WORKDIR /app\n"
    "COPY . .\n"
    'CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
)
VALID_COMPOSE = (
    "services:\n"
    "  app:\n"
    "    build: .\n"
    "    ports:\n"
    '      - "8000:8000"\n'
    "    environment:\n"
    "      DATABASE_URL: sqlite:///database.db\n"
)
VALID_WORKFLOW = (
    "name: CI\n"
    "on:\n"
    "  push:\n"
    "    branches: [main]\n"
    "jobs:\n"
    "  test:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - run: pytest\n"
)


def _saved_response() -> AgentArtifacts:
    return AgentArtifacts.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_saved_devops_response_normalizes_labels_and_rejects_content(tmp_path):
    artifacts = _saved_response()
    original = {item.relative_path: item.content for item in artifacts.files}

    result = validate_agent_artifacts(artifacts, agent_name="DevOps Engineer")

    assert result.valid is False
    assert "do not prove a Dockerfile will build" in result.limitation
    assert [item.language for item in artifacts.files] == ["dockerfile", "yaml", "yaml"]
    assert {item.relative_path: item.content for item in artifacts.files} == original
    assert any("incomplete JSON-array instruction" in issue and "CMD" in issue for issue in result.issues)
    assert any("sqlite:3" in issue for issue in result.issues)
    assert any("does not match the Dockerfile" in issue or "no complete CMD" in issue for issue in result.issues)
    assert not any("read as a boolean" in issue for issue in result.issues)
    assert not any("no recognizable test job" in issue for issue in result.issues)
    assert "CMD [" in original["Dockerfile"]
    assert "image: sqlite:3" in original["docker-compose.yml"]

    workspace = create_run_workspace("rundevops1", tmp_path)
    try:
        store_validated_artifacts(str(workspace), "DevOps Engineer", artifacts, [])
    except ArtifactValidationError as exc:
        assert exc.result.valid is False
    else:
        raise AssertionError("malformed DevOps artifacts were accepted")
    assert list(workspace.rglob("*")) == []


def test_saved_workflow_keeps_the_on_trigger():
    artifacts = _saved_response()
    workflow = next(item for item in artifacts.files if item.relative_path.endswith("ci.yml"))
    document = load_yaml_document(workflow.content)
    default_document = yaml.safe_load(workflow.content)

    assert "on" in document
    assert document["on"]["push"]["branches"] == ["main"]
    assert True in default_document
    assert "on" not in default_document
    assert document["jobs"]["build"]["steps"][-1]["run"] == "pytest"


def test_boolean_trigger_and_missing_tests_are_rejected():
    artifacts = _saved_response()
    artifacts.files[2].content = (
        "name: CI\n"
        "on: true\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo hello\n"
    )
    result = validate_agent_artifacts(artifacts, agent_name="DevOps Engineer")

    assert any("read as a boolean" in issue for issue in result.issues)
    assert any("no recognizable test job" in issue for issue in result.issues)


def test_language_aliases_and_a_consistent_devops_set_pass():
    artifacts = _saved_response()
    artifacts.files[0].content = VALID_DOCKERFILE
    artifacts.files[1].content = VALID_COMPOSE
    artifacts.files[1].language = "yml"
    artifacts.files[2].content = VALID_WORKFLOW
    artifacts.files[2].language = "yml"

    result = validate_agent_artifacts(artifacts, agent_name="DevOps Engineer")

    assert result.issues == []
    assert result.valid is True
    assert artifacts.files[1].language == "yaml"
    assert artifacts.files[2].language == "yaml"
    assert artifacts.files[1].content == VALID_COMPOSE


def test_py_alias_becomes_python_without_changing_source():
    artifacts = AgentArtifacts.model_validate(
        {
            "summary": "Backend files",
            "files": [
                {
                    "relative_path": "backend/main.py",
                    "content": "print('ok')\n",
                    "language": "py",
                    "source_agent": "Backend Engineer",
                }
            ],
        }
    )
    result = validate_agent_artifacts(artifacts, agent_name="Backend Engineer")

    assert result.valid is True
    assert artifacts.files[0].language == "python"
    assert artifacts.files[0].content == "print('ok')\n"


def test_compose_command_must_match_the_dockerfile_entrypoint():
    artifacts = _saved_response()
    artifacts.files[0].content = VALID_DOCKERFILE
    artifacts.files[1].content = (
        "services:\n"
        "  app:\n"
        "    build: .\n"
        "    command: [\"sleep\", \"10\"]\n"
    )
    artifacts.files[2].content = VALID_WORKFLOW
    result = validate_agent_artifacts(artifacts, agent_name="DevOps Engineer")

    assert result.valid is False
    assert any("does not match the Dockerfile runtime instruction" in issue for issue in result.issues)
