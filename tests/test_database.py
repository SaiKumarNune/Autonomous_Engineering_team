import sqlite3

from database.setup_db import create_database

EXPECTED_TABLES = {
    "projects",
    "agent_tasks",
    "generated_files",
    "code_reviews",
    "test_results",
    "user_preferences",
    "agent_logs",
    "workflow_runs",
    "agent_artifacts",
    "evaluation_results",
}


def test_database_file_can_be_created(tmp_path):
    db_file = tmp_path / "software_team.db"
    assert not db_file.exists()

    created = create_database(db_file)

    assert created == str(db_file)
    assert db_file.exists()


def test_required_tables_exist(tmp_path):
    db_file = create_database(tmp_path / "software_team.db")

    with sqlite3.connect(db_file) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

    tables = {row[0] for row in rows}
    assert EXPECTED_TABLES <= tables


def test_user_preferences_are_seeded(tmp_path):
    db_file = create_database(tmp_path / "software_team.db")

    with sqlite3.connect(db_file) as connection:
        rows = connection.execute(
            "SELECT preference_key, preference_value FROM user_preferences ORDER BY preference_key"
        ).fetchall()

    preferences = dict(rows)
    assert preferences["preferred_backend"] == "FastAPI"
    assert preferences["preferred_frontend"] == "Streamlit"
    assert preferences["preferred_database"] == "SQLite"
    assert preferences["preferred_testing"] == "pytest"
    assert preferences["preferred_deployment"] == "Docker"
    assert preferences["human_approval_required"] == "true"
    assert preferences["max_review_severity_allowed"] == "medium"
    assert preferences["preferred_llm"] == "ollama/llama3.1:8b"


def test_projects_table_accepts_a_project(tmp_path):
    db_file = create_database(tmp_path / "software_team.db")

    with sqlite3.connect(db_file) as connection:
        connection.execute(
            "INSERT INTO projects (project_name, user_request) VALUES (?, ?)",
            ("Sample Project", "Build a sample application"),
        )
        row = connection.execute(
            "SELECT project_name, user_request, target_backend, status FROM projects"
        ).fetchone()

    assert row == ("Sample Project", "Build a sample application", "FastAPI", "created")


def test_agent_tasks_can_be_inserted(tmp_path):
    db_file = create_database(tmp_path / "software_team.db")

    with sqlite3.connect(db_file) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            "INSERT INTO projects (project_name, user_request) VALUES (?, ?)",
            ("Sample Project", "Build a sample application"),
        )
        connection.execute(
            """
            INSERT INTO agent_tasks (project_id, agent_name, task_name, task_order)
            VALUES (?, ?, ?, ?)
            """,
            (cursor.lastrowid, "Product Manager Agent", "Extract requirements", 1),
        )
        row = connection.execute(
            "SELECT agent_name, task_name, status FROM agent_tasks"
        ).fetchone()

    assert row == ("Product Manager Agent", "Extract requirements", "pending")
