from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()

PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "product_manager_prompt.txt"
)


def _load_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"Could not load prompt from {PROMPT_PATH}: {exc}") from exc


def run_product_manager_agent(user_request: str) -> str:
    try:
        if not user_request or not user_request.strip():
            return "**Error:** user_request cannot be empty."

        ollama_model = os.getenv("OLLAMA_MODEL")
        if not ollama_model:
            return "**Error:** OLLAMA_MODEL environment variable is not set."

        prompt_text = _load_prompt()
        chat = ChatOllama(model=ollama_model)
        messages = [
            SystemMessage(content=prompt_text),
            HumanMessage(content=user_request),
        ]
        response = chat.invoke(messages)
        return (response.content or "").strip()
    except Exception as exc:
        return f"**Error:** {str(exc)}"
