# Autonomous Software Engineering Team

## Overview

Autonomous Software Engineering Team is a multi-agent AI platform that transforms a natural language software idea into a structured engineering workflow. The system uses specialized AI agents orchestrated through LangGraph and powered by local LLM inference using Ollama.

The platform simulates a real-world software engineering team by assigning responsibilities to dedicated agents that collaborate to generate requirements, architecture, backend code, frontend code, testing artifacts, DevOps configurations, and engineering reviews.

---

## Key Features

* Multi-agent software development workflow
* LangGraph-based agent orchestration
* Local LLM inference using Ollama
* Automated requirements generation
* System architecture design generation
* Backend API generation
* Frontend UI generation
* Automated pytest test creation
* DevOps and deployment artifact generation
* Engineering review and quality assessment
* SQLite persistence layer
* Streamlit-based user interface

---

## System Architecture

The workflow follows a sequential engineering pipeline:

User Idea
↓
Product Manager Agent
↓
Architect Agent
↓
Backend Engineer Agent
↓
Frontend Engineer Agent
↓
QA Engineer Agent
↓
DevOps Engineer Agent
↓
Reviewer Agent
↓
Generated Software Project

Each agent receives outputs from previous agents and contributes specialized engineering artifacts to the final solution.

---

## Agent Responsibilities

### Product Manager Agent

Responsible for:

* Requirement gathering
* Functional requirement generation
* Non-functional requirement generation
* User story creation
* Acceptance criteria definition

### Architect Agent

Responsible for:

* High-level architecture design
* Component identification
* API planning
* Database design recommendations
* Technology selection

### Backend Engineer Agent

Responsible for:

* FastAPI service generation
* API endpoint design
* Business logic generation
* Database integration code
* Backend project scaffolding

### Frontend Engineer Agent

Responsible for:

* Streamlit UI generation
* User workflow implementation
* Form generation
* Dashboard generation
* Frontend component creation

### QA Engineer Agent

Responsible for:

* Pytest test generation
* Functional testing
* Validation testing
* Edge-case testing
* Integration test generation

### DevOps Engineer Agent

Responsible for:

* Docker configuration generation
* Deployment scripts
* CI/CD configuration
* Infrastructure templates
* Environment setup files

### Reviewer Agent

Responsible for:

* Solution review
* Code quality assessment
* Architecture validation
* Improvement recommendations
* Final engineering feedback

---

## Technology Stack

### AI & Agent Framework

* LangGraph
* LangChain
* Ollama
* Llama 3.1 8B

### Application Layer

* Python
* Streamlit

### Data Layer

* SQLite

### Testing

* Pytest

### DevOps

* Docker
* CI/CD Templates

### Utilities

* Pydantic
* Python Dotenv

---

## Project Structure

```text
autonomous-software-engineering-team/
│
├── app.py
├── main.py
├── requirements.txt
├── README.md
├── .env
│
├── agents/
│   ├── product_manager_agent.py
│   ├── architect_agent.py
│   ├── backend_engineer_agent.py
│   ├── frontend_engineer_agent.py
│   ├── qa_engineer_agent.py
│   ├── devops_engineer_agent.py
│   └── reviewer_agent.py
│
├── workflows/
│   └── software_team_graph.py
│
├── prompts/
│   ├── product_manager_prompt.txt
│   ├── architect_prompt.txt
│   ├── backend_prompt.txt
│   ├── frontend_prompt.txt
│   ├── qa_prompt.txt
│   ├── devops_prompt.txt
│   └── reviewer_prompt.txt
│
├── tools/
│
├── database/
│   ├── setup_db.py
│   └── software_team.db
│
├── memory/
│
├── logs/
│
├── evals/
│
├── tests/
│
└── generated_projects/
```

---

## Database Schema

### projects

Stores user software requests.

| Column      | Type      |
| ----------- | --------- |
| id          | INTEGER   |
| title       | TEXT      |
| description | TEXT      |
| created_at  | TIMESTAMP |

### requirements

Stores generated requirements.

| Column       | Type    |
| ------------ | ------- |
| id           | INTEGER |
| project_id   | INTEGER |
| requirements | TEXT    |

### architectures

Stores generated architecture documents.

| Column       | Type    |
| ------------ | ------- |
| id           | INTEGER |
| project_id   | INTEGER |
| architecture | TEXT    |

### generated_artifacts

Stores generated outputs from agents.

| Column        | Type    |
| ------------- | ------- |
| id            | INTEGER |
| project_id    | INTEGER |
| artifact_type | TEXT    |
| content       | TEXT    |

---

## Installation

### Clone Repository

```bash
git clone <repository-url>
cd autonomous-software-engineering-team
```

### Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Ollama Setup

Install Ollama:

https://ollama.com

Pull model:

```bash
ollama pull llama3.1:8b
```

Verify:

```bash
ollama list
```

Expected:

```text
llama3.1:8b
```

---

## Initialize Database

```bash
python database/setup_db.py
```

---

## Run CLI Workflow

```bash
python main.py
```

Example:

```text
Build a task management application with FastAPI, Streamlit, SQLite, and Docker
```

---

## Run Streamlit UI

```bash
streamlit run app.py
```

---

## Example Prompts

* Build a task management platform with authentication and dashboards.
* Create a customer support ticketing system with analytics.
* Generate a healthcare claims processing platform.
* Build an e-commerce application with payments and inventory management.

---

## Future Enhancements

* Multi-agent parallel execution
* RAG-based engineering knowledge base
* Automatic repository creation
* Automatic code export
* GitHub integration
* Code execution sandbox
* Human-in-the-loop approval workflows

---

## Resume Highlights

* Built an autonomous multi-agent software engineering platform using LangGraph and Ollama to transform product ideas into full-stack engineering artifacts.
* Developed specialized Product Manager, Architect, Backend, Frontend, QA, DevOps, and Reviewer agents collaborating through a stateful workflow engine.
* Implemented local LLM-powered software generation using Llama 3.1 with agent orchestration, SQLite persistence, and Streamlit-based interaction.
* Automated requirement generation, architecture design, code generation, testing, DevOps artifact creation, and engineering review workflows.
