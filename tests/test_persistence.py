import sqlite3

from database.repository import persist_workflow_state
from tools.workspace import create_run_workspace, write_text_atomic


def test_persistence_records_run_and_redacts_secrets(tmp_path):
    workspace = create_run_workspace("runredact1", tmp_path / "generated")
    write_text_atomic(
        workspace,
        "backend/main.py",
        "token = supersecret\nprint('ok')\n",
    )
    state = {
        "run_id": "runredact1",
        "user_request": "Build a task manager",
        "status": "failed",
        "workspace_path": str(workspace),
        "requirements": "requirements",
        "architecture": "",
        "backend_code": "backend summary",
        "frontend_code": "",
        "qa_tests": "",
        "devops_files": "",
        "review": "",
        "errors": [{"agent": "Backend Engineer", "message": "token = supersecret"}],
        "artifact_manifest": [
            {
                "relative_path": "backend/main.py",
                "source_agent": "Backend Engineer",
                "language": "python",
                "size_bytes": 32,
                "sha256": "abc",
            }
        ],
        "validation_results": [
            {
                "agent": "Backend Engineer",
                "valid": False,
                "issues": ["syntax only"],
                "blocking": True,
                "kind": "artifact_validation",
                "limitation": "syntax is not correctness",
            }
        ],
    }

    project_id = persist_workflow_state(state, tmp_path / "team.db")

    with sqlite3.connect(tmp_path / "team.db") as connection:
        project = connection.execute(
            "SELECT id, run_id, user_request, status FROM projects"
        ).fetchone()
        workflow = connection.execute(
            "SELECT run_id, status, current_node FROM workflow_runs"
        ).fetchone()
        task = connection.execute(
            """
            SELECT status, error_message FROM agent_tasks
            WHERE agent_name = 'Backend Engineer'
            """
        ).fetchone()
        stored = connection.execute(
            "SELECT file_path, content, is_applied FROM generated_files"
        ).fetchone()
        evaluation = connection.execute(
            "SELECT metric_name, score FROM evaluation_results"
        ).fetchone()
        log = connection.execute("SELECT error_message FROM agent_logs").fetchone()

    assert project_id == project[0]
    assert project[1:] == ("runredact1", "Build a task manager", "failed")
    assert workflow == ("runredact1", "failed", "Backend Engineer")
    assert task[0] == "failed"
    assert "supersecret" not in task[1]
    assert stored[0] == "backend/main.py"
    assert stored[2] == 1
    assert "supersecret" not in stored[1]
    assert "[REDACTED]" in stored[1]
    assert "print('ok')" in stored[1]
    assert evaluation == ("artifact_validation", 0.0)
    assert "supersecret" not in log[0]
    assert "supersecret" in (workspace / "backend" / "main.py").read_text(encoding="utf-8")
