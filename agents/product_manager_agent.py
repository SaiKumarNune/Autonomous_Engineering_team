from agents.errors import AgentError
from agents.llm import invoke_agent
from agents.prompt_loader import load_prompt

AGENT_NAME = "Product Manager"


def run_product_manager_agent(user_request: str) -> str:
    """Turn a product idea into a requirements document."""
    if not user_request or not user_request.strip():
        raise AgentError(AGENT_NAME, "user_request cannot be empty.")
    return invoke_agent(
        AGENT_NAME,
        load_prompt("product_manager_prompt.txt"),
        user_request.strip(),
    )
