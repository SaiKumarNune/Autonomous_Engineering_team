import os
from typing import Optional

from dotenv import load_dotenv

try:
    # langchain and ChatOllama imports
    from langchain_ollama import ChatOllama
    from langchain_core.messages import HumanMessage, SystemMessage
except Exception:
    # If imports fail, we'll surface a helpful error at runtime
    ChatOllama = None
    HumanMessage = None
    SystemMessage = None


load_dotenv()


def _read_prompt(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def run_frontend_engineer_agent(architecture: str, requirements: str) -> str:
    """Generate Streamlit frontend files using LangChain Ollama.

    Returns a string containing FILE blocks for each generated file.
    """
    try:
        if ChatOllama is None:
            raise RuntimeError("langchain ChatOllama is not available. Ensure langchain and ollama packages are installed.")

        prompt_path = os.path.join(os.getcwd(), "prompts", "frontend_prompt.txt")
        prompt_text = _read_prompt(prompt_path)

        # Compose the system and user messages
        system_msg = SystemMessage(content=("You are a frontend engineer building a Streamlit app. "
                                           "Return all files in FILE blocks. Do not include extra commentary."))
        user_content = (
            f"Architecture:\n{architecture}\n\nRequirements:\n{requirements}\n\nPrompt:\n{prompt_text}"
        )
        user_msg = HumanMessage(content=user_content)

        # Initialize model. Expect OLLAMA_API_KEY or OLLAMA_BASE_URL in env if required.
        model = ChatOllama()
        response = model.predict_messages([system_msg, user_msg])

        text = response.content if hasattr(response, 'content') else str(response)

        # Ensure output contains FILE blocks; if not, wrap in a single FILE
        if "FILE" not in text:
            # default to a single app.py file
            file_block = (
                "FILE: app.py\n\n" + text
            )
            return file_block

        return text

    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


if __name__ == "__main__":
    # Quick manual test when run as a script
    arch = "simple"
    reqs = "streamlit; python; write a single-page UI"
    print(run_frontend_engineer_agent(arch, reqs))
