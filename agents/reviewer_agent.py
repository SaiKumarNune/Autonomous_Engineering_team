import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
PROMPT_PATH = ROOT_DIR / "prompts" / "reviewer_prompt.txt"


def _load_reviewer_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Could not find reviewer prompt at {PROMPT_PATH}") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to read reviewer prompt: {exc}") from exc


def run_reviewer_agent(all_outputs: str) -> str:
    """Run the reviewer agent and return a structured review in markdown."""
    try:
        prompt_text = _load_reviewer_prompt()
        all_outputs = all_outputs or ""

        model_name = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
        base_url = os.environ.get("OLLAMA_BASE_URL")
        ollama_kwargs = {"model": model_name}
        if base_url:
            ollama_kwargs["base_url"] = base_url

        ollama = ChatOllama(**ollama_kwargs)
        message = HumanMessage(content=f"{prompt_text}\n\n{all_outputs}")
        response = ollama([message])

        if response is None or not getattr(response, "content", None):
            raise RuntimeError("ChatOllama returned an empty response")

        return response.content.strip()
    except Exception as exc:
        return f"Error generating reviewer output: {exc}"
