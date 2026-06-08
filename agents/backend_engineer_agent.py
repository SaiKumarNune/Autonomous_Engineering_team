import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "backend_prompt.txt"
_DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")


def _load_prompt() -> str:
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(f"Prompt file not found: {_PROMPT_PATH}")

    prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Prompt file is empty: {_PROMPT_PATH}")

    return prompt


def run_backend_engineer_agent(architecture: str, requirements: str) -> str:
    try:
        prompt = _load_prompt()
        model = ChatOllama(model=_DEFAULT_MODEL)

        user_prompt = (
            f"{prompt}\n\n"
            "You are a backend engineer agent. Generate the backend files required for the requested architecture and requirements. "
            "Return generated backend files using FILE blocks."
            f"\n\nArchitecture:\n{architecture}\n\nRequirements:\n{requirements}"
        )

        response = model([
            SystemMessage(content="You are a backend engineer agent."),
            HumanMessage(content=user_prompt),
        ])

        content = getattr(response, "content", None)
        if content is None:
            content = str(response)

        return content.strip()
    except Exception as err:
        raise RuntimeError(f"Failed to run backend engineer agent: {err}") from err
