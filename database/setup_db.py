import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "software_team.db")


def create_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT NOT NULL,
        user_request TEXT NOT NULL,
        project_type TEXT DEFAULT 'full_stack_app',
        target_backend TEXT DEFAULT 'FastAPI',
        target_frontend TEXT DEFAULT 'Streamlit',
        target_database TEXT DEFAULT 'SQLite',
        target_deployment TEXT DEFAULT 'Docker',
        status TEXT NOT NULL DEFAULT 'created',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        agent_name TEXT NOT NULL,
        task_name TEXT NOT NULL,
        task_order INTEGER NOT NULL,
        input TEXT,
        output TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        error_message TEXT,
        started_at TEXT,
        completed_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS generated_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        agent_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_type TEXT NOT NULL,
        content TEXT NOT NULL,
        is_applied INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        UNIQUE(project_id, file_path)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS code_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        file_id INTEGER,
        reviewer_agent TEXT NOT NULL DEFAULT 'Reviewer Agent',
        review_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        feedback TEXT NOT NULL,
        recommendation TEXT,
        resolved INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY (file_id) REFERENCES generated_files(id) ON DELETE SET NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS test_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        test_name TEXT NOT NULL,
        test_type TEXT NOT NULL,
        status TEXT NOT NULL,
        command TEXT,
        output TEXT,
        passed_count INTEGER DEFAULT 0,
        failed_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        preference_key TEXT NOT NULL UNIQUE,
        preference_value TEXT NOT NULL,
        description TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        agent_name TEXT NOT NULL,
        event_type TEXT NOT NULL,
        tool_used TEXT,
        input TEXT,
        output TEXT,
        latency_ms INTEGER,
        token_estimate INTEGER,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS workflow_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        workflow_name TEXT NOT NULL,
        current_node TEXT,
        status TEXT NOT NULL DEFAULT 'running',
        state_json TEXT,
        started_at TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT,
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        agent_name TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS evaluation_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        evaluation_name TEXT NOT NULL,
        metric_name TEXT NOT NULL,
        score REAL NOT NULL,
        max_score REAL NOT NULL DEFAULT 1.0,
        notes TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
    )
    """)

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_project_id ON agent_tasks(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent_name ON agent_tasks(agent_name)",
        "CREATE INDEX IF NOT EXISTS idx_generated_files_project_id ON generated_files(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_generated_files_file_path ON generated_files(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_code_reviews_project_id ON code_reviews(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_test_results_project_id ON test_results(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_logs_project_id ON agent_logs(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_name ON agent_logs(agent_name)",
        "CREATE INDEX IF NOT EXISTS idx_workflow_runs_project_id ON workflow_runs(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_agent_artifacts_project_id ON agent_artifacts(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_evaluation_results_project_id ON evaluation_results(project_id)"
    ]

    for index_sql in indexes:
        cursor.execute(index_sql)

    preferences = [
        ("preferred_llm", "ollama/llama3.1:8b", "Default local LLM used by all software engineering agents."),
        ("preferred_backend", "FastAPI", "Default backend framework generated by Backend Engineer Agent."),
        ("preferred_frontend", "Streamlit", "Default UI framework generated by Frontend Engineer Agent."),
        ("preferred_database", "SQLite", "Default database for generated projects and local development."),
        ("preferred_testing", "pytest", "Default Python testing framework."),
        ("preferred_deployment", "Docker", "Default packaging and deployment strategy."),
        ("human_approval_required", "true", "Require human approval before writing final generated project files."),
        ("max_review_severity_allowed", "medium", "Blocks deployment recommendation if high or critical issues exist.")
    ]

    cursor.executemany("""
    INSERT OR IGNORE INTO user_preferences (preference_key, preference_value, description)
    VALUES (?, ?, ?)
    """, preferences)

    conn.commit()
    conn.close()
    print(f"Database created successfully at: {DB_PATH}")


if __name__ == "__main__":
    create_database()
