from agents.errors import AgentError
from agents.llm import invoke_agent
from agents.prompt_loader import load_prompt

AGENT_NAME = "Reviewer"


def run_reviewer_agent(all_outputs: str) -> str:
    """Review the combined requirements, design, code, tests, and DevOps text."""
    if not all_outputs or not all_outputs.strip():
        raise AgentError(AGENT_NAME, "all_outputs cannot be empty.")
    return invoke_agent(
        AGENT_NAME,
        load_prompt("reviewer_prompt.txt"),
        all_outputs.strip(),
    )
