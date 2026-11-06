from agents.artifacts import AgentArtifacts
from agents.errors import AgentError
from agents.llm import invoke_structured_agent
from agents.prompt_loader import load_prompt

AGENT_NAME = "Backend Engineer"


def run_backend_engineer_agent(
    architecture: str,
    requirements: str,
    correction: str = "",
) -> AgentArtifacts:
    """Generate backend source from the architecture and requirements."""
    if not architecture or not architecture.strip():
        raise AgentError(AGENT_NAME, "architecture cannot be empty.")
    if not requirements or not requirements.strip():
        raise AgentError(AGENT_NAME, "requirements cannot be empty.")
    user_content = (
        f"Architecture:\n{architecture.strip()}\n\n"
        f"Requirements:\n{requirements.strip()}"
    )
    if correction and correction.strip():
        user_content = f"{user_content}\n\n{correction.strip()}"
    return invoke_structured_agent(
        AGENT_NAME,
        load_prompt("backend_prompt.txt"),
        user_content,
    )


def backend_sqlite_repair_request(issues: list[str], artifacts: AgentArtifacts) -> str:
    """Ask for one correction that includes the rejected source and the contract error."""
    blocks = [f"PATH: {item.relative_path}\n{item.content}" for item in artifacts.files]
    original = "\n\n".join(blocks) if blocks else "(no files were returned)"
    listed = "\n".join(f"- {issue}" for issue in issues)
    return (
        "The previous backend artifacts failed validation before they were written.\n"
        f"Validation errors:\n{listed}\n\n"
        "Original artifacts:\n"
        f"{original}\n\n"
        "Return the complete files again. Python source must import sqlite3 or contain "
        "a string literal that includes sqlite, such as sqlite:///./tasks.db. "
        "An environment variable without that literal does not pass. "
        "Request and response bodies must use Pydantic BaseModel schemas. "
        "Do not annotate a FastAPI parameter, return type, or response_model with a "
        "SQLAlchemy ORM class. "
        "Keep one module-level FastAPI() application and the pinned requirements.txt."
    )
