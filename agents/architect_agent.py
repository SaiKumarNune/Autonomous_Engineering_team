import os
from pathlib import Path
from dotenv import load_dotenv
from langchain_ollama import ChatOllama

# Load environment variables
load_dotenv()


def run_architect_agent(requirements: str) -> str:
    """
    Run the architect agent to generate structured architecture markdown.
    
    Args:
        requirements: The system requirements or specifications
        
    Returns:
        Structured architecture markdown as a string
        
    Raises:
        FileNotFoundError: If prompt file is not found
        Exception: For other runtime errors
    """
    try:
        # Load the architect prompt
        prompt_path = Path(__file__).parent.parent / "prompts" / "architect_prompt.txt"
        
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt file not found at {prompt_path}")
        
        with open(prompt_path, "r") as f:
            system_prompt = f.read()
        
        # Initialize ChatOllama
        model = ChatOllama(model="llama3.1:8b")
        
        # Create the message
        user_message = f"{system_prompt}\n\nRequirements:\n{requirements}"
        
        # Generate architecture
        response = model.invoke(user_message)
        
        # Extract and return the content
        architecture = response.content
        
        return architecture
        
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Failed to load prompt: {str(e)}")
    except Exception as e:
        raise Exception(f"Error running architect agent: {str(e)}")
