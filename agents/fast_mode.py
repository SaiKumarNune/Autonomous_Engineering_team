"""Deterministic stand-ins for local development. These are not model output."""

from __future__ import annotations

import os

from agents.artifacts import AgentArtifacts, GeneratedFile
from execution.dependencies import requirements_text

SIMULATED_LABEL = "SIMULATED FAST_DEV_MODE output. Not real model output."


def fast_dev_mode() -> bool:
    return os.getenv("FAST_DEV_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def simulated_text(agent_name: str, user_content: str) -> str:
    excerpt = " ".join(user_content.split())[:180]
    if agent_name == "Repair":
        return simulated_artifacts(agent_name).model_dump_json()
    return (
        f"{SIMULATED_LABEL}\n"
        f"Agent: {agent_name}\n"
        "Requirements\n"
        "Acceptance criteria\n"
        "Constraints\n"
        "Assumptions\n"
        f"Request excerpt: {excerpt}\n"
    )


def simulated_artifacts(agent_name: str) -> AgentArtifacts:
    label = SIMULATED_LABEL
    files = {
        "QA Engineer": (
            "tests/test_api.py",
            "python",
            "import sys\n"
            "from pathlib import Path\n"
            "\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
            "from fastapi.testclient import TestClient\n"
            "from backend.main import app\n"
            "\n"
            "def test_health():\n"
            "    with TestClient(app) as client:\n"
            "        response = client.get('/health')\n"
            "    assert response.status_code == 200\n"
            "    assert 'SIMULATED FAST_DEV_MODE' in response.json()['label']\n"
            "    assert 'Not real model output' in response.json()['label']\n",
        ),
        "Repair": (
            "backend/main.py",
            "python",
            f"print({label!r})\n",
        ),
    }
    if agent_name == "Frontend Engineer":
        return AgentArtifacts(
            summary=(
                f"{label} Backend-only FastAPI project. "
                "No frontend files were generated. Not real model output."
            ),
            files=[],
        )
    if agent_name == "Backend Engineer":
        return AgentArtifacts(
            summary=f"{label} Agent: {agent_name}",
            files=[
                GeneratedFile(
                    relative_path="backend/main.py",
                    content=(
                        "import os\n"
                        "from fastapi import FastAPI\n"
                        "from sqlalchemy import create_engine\n"
                        "DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///./sim.db')\n"
                        "engine = create_engine(DATABASE_URL)\n"
                        "app = FastAPI()\n"
                        "@app.get('/health')\n"
                        "def health():\n"
                        f"    return {{'label': {label!r}}}\n"
                    ),
                    language="python",
                    source_agent=agent_name,
                ),
                GeneratedFile(
                    relative_path="requirements.txt",
                    content=requirements_text(),
                    language="text",
                    source_agent=agent_name,
                ),
            ],
        )
    if agent_name == "DevOps Engineer":
        return AgentArtifacts(
            summary=f"{label} Agent: {agent_name}",
            files=[
                GeneratedFile(
                    relative_path="Dockerfile",
                    content=(
                        "FROM python:3.12-slim\n"
                        "WORKDIR /app\n"
                        "COPY . .\n"
                        'CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]\n'
                        f"# {label}\n"
                    ),
                    language="dockerfile",
                    source_agent=agent_name,
                ),
                GeneratedFile(
                    relative_path="docker-compose.yml",
                    content=(
                        f"# {label}\n"
                        "services:\n"
                        "  app:\n"
                        "    build: .\n"
                        "    ports:\n"
                        '      - "8000:8000"\n'
                        "    environment:\n"
                        "      DATABASE_URL: sqlite:///database.db\n"
                    ),
                    language="yaml",
                    source_agent=agent_name,
                ),
                GeneratedFile(
                    relative_path=".github/workflows/ci.yml",
                    content=(
                        f"# {label}\n"
                        "name: CI\n"
                        "on:\n"
                        "  push:\n"
                        "    branches: [main]\n"
                        "jobs:\n"
                        "  test:\n"
                        "    runs-on: ubuntu-latest\n"
                        "    steps:\n"
                        "      - run: pytest\n"
                    ),
                    language="yaml",
                    source_agent=agent_name,
                ),
            ],
        )
    path, language, content = files.get(
        agent_name,
        ("notes.txt", "text", f"{label}\n"),
    )
    return AgentArtifacts(
        summary=f"{label} Agent: {agent_name}",
        files=[
            GeneratedFile(
                relative_path=path,
                content=content,
                language=language,
                source_agent=agent_name,
            )
        ],
    )
