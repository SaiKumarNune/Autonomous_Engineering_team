import json

import pytest

from agents.artifacts import AgentArtifacts, GeneratedFile
from agents.errors import AgentError
from agents.structured_output import parse_agent_artifacts
from tools.validators import validate_agent_artifacts


def _artifacts(agent: str, path: str, content: str, language: str) -> AgentArtifacts:
    return AgentArtifacts(
        files=[
            GeneratedFile(
                relative_path=path,
                content=content,
                language=language,
                source_agent=agent,
            )
        ],
        summary="Generated files",
    )


def test_parse_accepts_fenced_json():
    payload = json.dumps(
        {
            "summary": "Backend files",
            "files": [
                {
                    "relative_path": "backend/main.py",
                    "content": "print('ok')\n",
                    "language": "python",
                    "source_agent": "Backend Engineer",
                }
            ],
        }
    )
    parsed = parse_agent_artifacts("Backend Engineer", f"```json\n{payload}\n```")
    assert parsed.files[0].relative_path == "backend/main.py"


def test_parse_rejects_mismatched_source_agent():
    payload = {
        "summary": "Backend files",
        "files": [
            {
                "relative_path": "backend/main.py",
                "content": "print('ok')\n",
                "language": "python",
                "source_agent": "Someone Else",
            }
        ],
    }
    with pytest.raises(AgentError, match="source_agent"):
        parse_agent_artifacts("Backend Engineer", json.dumps(payload))


def test_duplicate_paths_are_invalid():
    artifacts = AgentArtifacts(
        summary="duplicates",
        files=[
            GeneratedFile(
                relative_path="backend/main.py",
                content="print('a')\n",
                language="python",
                source_agent="Backend Engineer",
            ),
            GeneratedFile(
                relative_path="backend/main.py",
                content="print('b')\n",
                language="python",
                source_agent="Backend Engineer",
            ),
        ],
    )
    result = validate_agent_artifacts(artifacts, agent_name="Backend Engineer")
    assert result.valid is False
    assert any("duplicate" in issue for issue in result.issues)


def test_invalid_python_and_json_syntax_are_reported():
    artifacts = AgentArtifacts(
        summary="broken",
        files=[
            GeneratedFile(
                relative_path="backend/main.py",
                content="def (\n",
                language="python",
                source_agent="Backend Engineer",
            ),
            GeneratedFile(
                relative_path="backend/settings.json",
                content="{",
                language="json",
                source_agent="Backend Engineer",
            ),
        ],
    )
    result = validate_agent_artifacts(artifacts, agent_name="Backend Engineer")
    assert result.valid is False
    assert any("ast.parse does not prove" in issue for issue in result.issues)
    assert any("json.loads does not prove" in issue for issue in result.issues)


def test_missing_required_artifact_and_size_limit():
    artifacts = _artifacts("Backend Engineer", "README.md", "hello", "markdown")
    missing = validate_agent_artifacts(artifacts, agent_name="Backend Engineer")
    assert missing.valid is False
    assert any("Missing required" in issue for issue in missing.issues)

    oversized = _artifacts("Backend Engineer", "backend/main.py", "print('ok')\n", "python")
    limited = validate_agent_artifacts(
        oversized,
        agent_name="Backend Engineer",
        max_bytes=4,
    )
    assert limited.valid is False
    assert any("exceeds" in issue for issue in limited.issues)


def test_path_escape_is_invalid():
    artifacts = _artifacts("Backend Engineer", "../outside.py", "print('ok')\n", "python")
    result = validate_agent_artifacts(artifacts, agent_name="Backend Engineer")
    assert result.valid is False
    assert any("escapes" in issue or "disallowed" in issue for issue in result.issues)
