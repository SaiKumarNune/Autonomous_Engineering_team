from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from agents.artifacts import ArtifactValidationResult


class AgentErrorRecord(TypedDict):
    agent: str
    message: str


def with_correction(user_content: str, correction: str) -> str:
    """Append a syntax rejection so the next model call can repair the files."""
    if not correction or not str(correction).strip():
        return user_content
    return (
        f"{user_content}\n\n"
        "The previous files were rejected by syntax validation:\n"
        f"{str(correction).strip()}\n"
        "Return the files again. Every Python suite after if, elif, else, for, "
        "while, def, class, try, and except must contain an indented statement."
    )


class AgentError(RuntimeError):
    """Failure from an agent, its prompt, or its model call."""

    def __init__(self, agent_name: str, message: str) -> None:
        self.agent_name = agent_name
        self.message = message
        super().__init__(f"{agent_name}: {message}")

    def to_record(self) -> AgentErrorRecord:
        return {"agent": self.agent_name, "message": self.message}


class ArtifactValidationError(AgentError):
    """Raised when artifacts fail a blocking validation check."""

    def __init__(self, agent_name: str, result: ArtifactValidationResult) -> None:
        self.result = result
        message = "; ".join(result.issues) or "Artifact validation failed."
        super().__init__(agent_name, message)

    def validation_record(self) -> dict[str, object]:
        return {
            "agent": self.agent_name,
            "valid": self.result.valid,
            "issues": list(self.result.issues),
            "blocking": True,
            "limitation": self.result.limitation,
            "kind": "artifact_validation",
        }
