from agents.errors import AgentError
from agents.llm import invoke_agent
from agents.prompt_loader import load_prompt

AGENT_NAME = "Architect"


def run_architect_agent(requirements: str) -> str:
    """Turn requirements into an architecture proposal."""
    if not requirements or not requirements.strip():
        raise AgentError(AGENT_NAME, "requirements cannot be empty.")
    return invoke_agent(
        AGENT_NAME,
        load_prompt("architect_prompt.txt"),
        f"Requirements:\n{requirements.strip()}",
    )
