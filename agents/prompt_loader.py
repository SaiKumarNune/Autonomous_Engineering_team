from __future__ import annotations

from pathlib import Path

from agents.errors import AgentError

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(filename: str) -> str:
    """Load a prompt from the repository prompts directory."""
    if not filename or Path(filename).name != filename:
        raise AgentError(
            "Prompt loader",
            "Prompt name must be a file name inside the prompts directory.",
        )

    path = PROMPTS_DIR / filename
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise AgentError("Prompt loader", f"Prompt file not found: {filename}") from exc
    except OSError as exc:
        raise AgentError(
            "Prompt loader",
            f"Unable to read prompt {filename}: {exc}",
        ) from exc

    if not text:
        raise AgentError("Prompt loader", f"Prompt file is empty: {filename}")
    return text
