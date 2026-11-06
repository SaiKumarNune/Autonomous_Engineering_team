from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GeneratedFile(BaseModel):
    """One file an agent wants written inside the run workspace."""

    model_config = ConfigDict(extra="ignore")

    relative_path: str = Field(min_length=1)
    content: str
    language: str = Field(min_length=1)
    source_agent: str = Field(min_length=1)


class AgentArtifacts(BaseModel):
    """Structured files and a short summary from one agent."""

    model_config = ConfigDict(extra="ignore")

    files: list[GeneratedFile] = Field(default_factory=list)
    summary: str = Field(min_length=1)


class ArtifactValidationResult(BaseModel):
    """Result of checking artifacts before they are written.

    Syntax checks do not prove that the generated code behaves correctly.
    """

    model_config = ConfigDict(extra="ignore")

    valid: bool
    issues: list[str] = Field(default_factory=list)
    limitation: str = (
        "Python ast.parse, json.loads, and static Dockerfile, Compose, and GitHub Actions "
        "checks look for structure only. They do not prove the code is correct, secure, or "
        "runnable, and they do not prove a Dockerfile will build."
    )
