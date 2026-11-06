from agents.artifacts import AgentArtifacts
from agents.errors import AgentError, with_correction
from agents.llm import invoke_structured_agent
from agents.prompt_loader import load_prompt

AGENT_NAME = "QA Engineer"


def run_qa_engineer_agent(
    generated_code: str,
    requirements: str,
    correction: str = "",
) -> AgentArtifacts:
    """Generate pytest files for the generated backend and frontend artifacts."""
    if not generated_code or not generated_code.strip():
        raise AgentError(AGENT_NAME, "generated_code cannot be empty.")
    if not requirements or not requirements.strip():
        raise AgentError(AGENT_NAME, "requirements cannot be empty.")
    user_content = (
        f"Generated files:\n{generated_code.strip()}\n\n"
        f"Requirements:\n{requirements.strip()}\n\n"
        "Generate pytest files for the generated backend and frontend artifacts."
    )
    user_content = with_correction(user_content, correction)
    return invoke_structured_agent(AGENT_NAME, load_prompt("qa_prompt.txt"), user_content)
