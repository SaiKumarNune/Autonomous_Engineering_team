import ast
import re

from agents.artifacts import AgentArtifacts, ArtifactValidationResult
from agents.errors import AgentError, ArtifactValidationError
from agents.fast_mode import fast_dev_mode, simulated_artifacts
from agents.llm import invoke_structured_agent
from agents.prompt_loader import load_prompt

AGENT_NAME = "Frontend Engineer"
_UI_MARKERS = ("streamlit", "react", "frontend", "user interface", "web ui", "html")
_GENERAL_UI_REFUSAL = re.compile(
    r"\b(?:backend-only|backend only|no frontend|without(?: a)? frontend|"
    r"no user interface|no web ui|without(?: a)? (?:user interface|web ui|ui)|no ui)\b"
)
_NEGATED_UI_MARKER = re.compile(
    r"\b(?:no|without(?: a)?)\s+(?:streamlit|react|frontend|user interface|web ui|html|ui)\b"
)


def run_frontend_engineer_agent(
    architecture: str,
    requirements: str,
    backend_artifacts: str,
    correction: str = "",
    user_request: str = "",
) -> AgentArtifacts:
    """Generate the Streamlit frontend from the architecture and backend artifacts."""
    if not architecture or not architecture.strip():
        raise AgentError(AGENT_NAME, "architecture cannot be empty.")
    if not requirements or not requirements.strip():
        raise AgentError(AGENT_NAME, "requirements cannot be empty.")
    if not backend_artifacts or not backend_artifacts.strip():
        raise AgentError(AGENT_NAME, "backend_artifacts cannot be empty.")
    user_content = (
        f"Architecture:\n{architecture.strip()}\n\n"
        f"Requirements:\n{requirements.strip()}\n\n"
        f"Backend artifacts:\n{backend_artifacts.strip()}"
    )
    if fast_dev_mode():
        return simulated_artifacts(AGENT_NAME)
    if _backend_only(architecture, requirements, backend_artifacts, user_request):
        return AgentArtifacts(
            summary=(
                "Backend-only FastAPI project. No frontend files were generated. "
                "Frontend generation remains available when a user interface is requested."
            ),
            files=[],
        )
    if correction and correction.strip():
        user_content = f"{user_content}\n\n{correction.strip()}"
    return invoke_structured_agent(
        AGENT_NAME,
        load_prompt("frontend_prompt.txt"),
        user_content,
    )


def _backend_only(
    architecture: str,
    requirements: str,
    backend_artifacts: str,
    user_request: str = "",
) -> bool:
    """True when the backend is FastAPI and the original request does not ask for a UI.

    The architect prompt always includes a Frontend design heading, so that
    generated text is not treated as a request for a user interface.
    """
    if "fastapi" not in backend_artifacts.lower():
        return False
    text = user_request.strip() or f"{architecture}\n{requirements}"
    return not _requests_user_interface(text)


def _requests_user_interface(text: str) -> bool:
    """True when the request asks for a UI and does not explicitly refuse one.

    A backend-only or no-frontend instruction wins over a positive UI keyword.
    A narrower refusal, such as "no Streamlit", removes only that marker.
    """
    lowered = text.lower()
    if _GENERAL_UI_REFUSAL.search(lowered):
        return False
    remaining = _NEGATED_UI_MARKER.sub(" ", lowered)
    return any(marker in remaining for marker in _UI_MARKERS)


def frontend_syntax_repair_request(syntax_error: str, artifacts: AgentArtifacts) -> str:
    """Ask Ollama for corrected structured output, including the invalid source."""
    blocks = [
        f"PATH: {item.relative_path}\n{item.content}"
        for item in artifacts.files
        if item.relative_path.endswith(".py") or item.language.lower() == "python"
    ]
    invalid = "\n\n".join(blocks) if blocks else "(no Python file was returned)"
    return (
        "The frontend Python failed ast.parse().\n"
        f"SyntaxError: {syntax_error.strip()}\n\n"
        "Invalid code:\n"
        f"{invalid}\n\n"
        "Return corrected structured output as one JSON object. "
        "Every source_agent must be 'Frontend Engineer'. "
        "Do not return the invalid code unchanged."
    )


def accept_frontend_artifacts(artifacts: AgentArtifacts) -> AgentArtifacts:
    """Validate with Pydantic and ast.parse before any file is written."""
    if not isinstance(artifacts, AgentArtifacts):
        raise AgentError(AGENT_NAME, "Agent did not return AgentArtifacts.")
    checked = AgentArtifacts.model_validate(artifacts.model_dump())
    issues: list[str] = []
    for item in checked.files:
        if not (item.relative_path.endswith(".py") or item.language.lower() == "python"):
            continue
        try:
            ast.parse(item.content)
        except SyntaxError as exc:
            line = exc.lineno or 1
            issues.append(
                f"{item.relative_path}: Python syntax error at line {line}: {exc.msg}."
            )
    if issues:
        raise ArtifactValidationError(
            AGENT_NAME,
            ArtifactValidationResult(valid=False, issues=issues),
        )
    return checked
