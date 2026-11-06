# Autonomous Software Engineering Team

Seven specialized agents turn a software request into requirements, architecture, backend code, an optional frontend, tests, and DevOps files. A LangGraph workflow runs the agents in order, checks their files, and executes generated tests in a Docker sandbox. A passing run stops at `awaiting_approval`. Nothing is approved automatically.

## Agents

The graph keeps these seven roles:

1. **Product Manager** writes the requirements.
2. **Architect** writes the architecture.
3. **Backend Engineer** writes the backend and `requirements.txt`.
4. **Frontend Engineer** writes a UI only when one is requested. A backend-only request, including “no frontend,” “no UI,” “without a frontend,” or “no Streamlit,” produces no frontend files.
5. **QA Engineer** writes pytest tests.
6. **DevOps Engineer** selects the deterministic FastAPI + SQLite templates for `Dockerfile`, `docker-compose.yml`, and `.github/workflows/ci.yml`. It does not ask the model to invent those files.
7. **Reviewer** reads the saved artifacts and sandbox results.

LangGraph also runs the sandbox, a bounded repair step, and an approval interrupt. Those steps are not extra agents. After DevOps, the flow is sandbox tests, repair when the existing repair rules apply, Reviewer, then the approval interrupt.

## Validation

Generated files are checked before they are written.

- Backend code must have one module-level FastAPI application, SQLite evidence (`sqlite3` or a Python string containing `sqlite`), and exact pins for the sandbox packages.
- A SQLAlchemy ORM class cannot be used as a FastAPI route parameter, return annotation, or `response_model`. Request and response bodies use Pydantic schemas.
- A missing SQLite configuration or an invalid ORM annotation can trigger one Backend correction. The replacement is checked again before it is written.
- QA tests must be collectable pytest modules and may import only the standard library, local modules, and the approved packages.
- DevOps files are rendered from `templates/fastapi_sqlite` for one FastAPI application that uses SQLite. Unsupported frameworks and database servers are rejected. The template does not add a SQLite server container.

`FAST_DEV_MODE` (`1`, `true`, `yes`, or `on`) skips the model and returns labeled simulated files. That output is not a real Ollama run.

## Docker sandbox

Generated pytest runs only inside the `autonomous-team-sandbox:local` image. The container uses `--network none`, drops all capabilities, sets `no-new-privileges`, runs as user `65534:65534`, and mounts the project read-only. Memory, CPU, process, and output limits apply. The command is the fixed pytest command. If Docker or a required control is missing, the run records a sandbox error and does not execute the generated code on the host.

The image contains the pinned test packages: pytest 8.4.1, fastapi 0.115.12, uvicorn 0.34.0, sqlalchemy 2.0.40, httpx 0.28.1, and pydantic 2.11.7. Generated `requirements.txt` is classified. It is not installed during the test run. The project root is placed on `PYTHONPATH` inside the container.

Docker is not a complete security boundary. A passing sandbox test does not prove the application is correct, secure, or ready to deploy. See `sandbox/THREAT_MODEL.md`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
docker build -t autonomous-team-sandbox:local sandbox
```

Install Ollama and pull `llama3.1:8b`. The model settings are `OLLAMA_MODEL` and `OLLAMA_BASE_URL` (the local default is `http://localhost:11434`). Do not commit a `.env` file.

Run the Streamlit UI:

```bash
streamlit run app.py
```

Run one workflow from the terminal:

```bash
python main.py
```

Run the project tests, including the Docker integration tests when Docker is available:

```bash
pytest
```

## Current limitations

- A real local Ollama run has not reached `awaiting_approval`. The latest real run, `phase4final1`, passed backend validation and then failed when the Frontend Engineer returned backend-owned files. The backend-only routing check was corrected after that run. The run was not repeated.
- Saved failed checkpoints are terminal. Resuming one does not regenerate the earlier agents and does not continue a finished failure safely.
- The sandbox workspace is read-only. An application that opens `sqlite:///./tasks.db` cannot create that file there.
- The sandbox does not provide Streamlit, and a requested Streamlit UI is not executed by the sandbox.
- Human approval is required after a passing sandbox run. The workflow does not approve, push, or deploy its own output.
