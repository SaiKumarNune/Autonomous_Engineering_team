import os
import re
from pathlib import Path
from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_core.prompts import PromptTemplate


# Load environment variables
load_dotenv()


def run_devops_engineer_agent(project_files_summary: str) -> str:
    """
    Run the DevOps engineer agent to generate Docker, docker-compose, and GitHub Actions files.
    
    Args:
        project_files_summary: Summary of project files and structure
        
    Returns:
        Generated DevOps configuration files as string with FILE blocks
    """
    try:
        # Load the DevOps prompt
        prompt_path = Path(__file__).parent.parent / "prompts" / "devops_prompt.txt"
        
        if not prompt_path.exists():
            return f"Error: Prompt file not found at {prompt_path}"
        
        with open(prompt_path, 'r') as f:
            prompt_template_str = f.read()
        
        # Initialize ChatOllama
        llm = ChatOllama(
            model=os.getenv("OLLAMA_MODEL", "mistral"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.3,
        )
        
        # Create prompt template
        prompt = PromptTemplate(
            template=prompt_template_str,
            input_variables=["project_files_summary"]
        )
        
        # Format the prompt with project summary
        formatted_prompt = prompt.format(project_files_summary=project_files_summary)
        
        # Run the agent
        response = llm.invoke(formatted_prompt)
        
        # Extract the response content
        if hasattr(response, 'content'):
            result = response.content
        else:
            result = str(response)
        
        return result
        
    except FileNotFoundError as e:
        return f"Error: File not found - {str(e)}"
    except Exception as e:
        return f"Error running DevOps engineer agent: {str(e)}"
