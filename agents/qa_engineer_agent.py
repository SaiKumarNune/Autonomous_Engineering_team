import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

load_dotenv()

PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "qa_prompt.txt"


def _load_prompt():
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(f"Prompt file not found: {PROMPT_FILE}")

    return PROMPT_FILE.read_text(encoding="utf-8")


def run_qa_engineer_agent(generated_code: str, requirements: str) -> str:
    prompt_text = _load_prompt()

    final_prompt = f"""
{prompt_text}

Generated Code:
{generated_code}

Requirements:
{requirements}

Generate pytest test cases.

Return only test files.
"""

    llm = ChatOllama(
        model="llama3.1:8b",
        temperature=0
    )

    result = llm.invoke(final_prompt)

    return result.content