import pathlib
import sqlite3


def initialize_database(db_path):
    db_path = pathlib.Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    with connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY,
                theme TEXT NOT NULL DEFAULT 'light',
                notifications_enabled INTEGER NOT NULL DEFAULT 1,
                language TEXT NOT NULL DEFAULT 'en'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS starter_project (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id INTEGER PRIMARY KEY,
                task_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        connection.execute("INSERT INTO user_preferences DEFAULT VALUES")
    connection.close()
    return db_path


def test_database_file_can_be_created(tmp_path):
    db_file = tmp_path / "autonomous.db"
    assert not db_file.exists()
    initialize_database(db_file)
    assert db_file.exists()


def test_required_tables_exist(tmp_path):
    db_file = tmp_path / "autonomous.db"
    initialize_database(db_file)
    with sqlite3.connect(str(db_file)) as connection:
        cursor = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor}

    assert {"user_preferences", "starter_project", "agent_tasks"}.issubset(tables)


def test_user_preferences_has_default_values(tmp_path):
    db_file = tmp_path / "autonomous.db"
    initialize_database(db_file)
    with sqlite3.connect(str(db_file)) as connection:
        cursor = connection.execute(
            "SELECT theme, notifications_enabled, language FROM user_preferences LIMIT 1"
        )
        row = cursor.fetchone()

    assert row == ("light", 1, "en")


def test_starter_project_can_be_inserted(tmp_path):
    db_file = tmp_path / "autonomous.db"
    initialize_database(db_file)
    with sqlite3.connect(str(db_file)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO starter_project (name, description) VALUES (?, ?)",
                ("Sample Project", "A starter project for testing"),
            )
        cursor = connection.execute(
            "SELECT name, description FROM starter_project WHERE name = ?",
            ("Sample Project",),
        )
        row = cursor.fetchone()

    assert row == ("Sample Project", "A starter project for testing")


def test_agent_tasks_can_be_inserted(tmp_path):
    db_file = tmp_path / "autonomous.db"
    initialize_database(db_file)
    with sqlite3.connect(str(db_file)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO agent_tasks (task_name, status) VALUES (?, ?)",
                ("Initial task", "pending"),
            )
        cursor = connection.execute(
            "SELECT task_name, status FROM agent_tasks WHERE task_name = ?",
            ("Initial task",),
        )
        row = cursor.fetchone()

    assert row == ("Initial task", "pending")
