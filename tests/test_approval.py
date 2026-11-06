import sqlite3

from database.checkpoint import SqliteCheckpointSaver
from database.repository import latest_approval
from tests.test_workflow import _run
from workflows.software_team_graph import build_software_team_graph, resume_software_team_workflow
import workflows.software_team_graph as workflow


def test_approval_survives_a_new_process_and_matches_the_hash(monkeypatch, tmp_path):
    _calls, state = _run(monkeypatch, tmp_path)
    database = tmp_path / "team.db"
    config = {"configurable": {"thread_id": state["run_id"]}}

    restarted = SqliteCheckpointSaver(str(database))
    snapshot = build_software_team_graph(checkpointer=restarted).get_state(config)

    assert snapshot.next == ("approval",)
    assert snapshot.values["artifact_hash"] == state["artifact_hash"]

    approved = resume_software_team_workflow(
        state["run_id"],
        approved=True,
        artifact_hash=state["artifact_hash"],
        db_path=database,
    )

    assert approved["status"] == "approved"
    assert approved["lifecycle_status"] == "approved"
    assert approved["errors"] == []
    assert latest_approval(database, state["run_id"], state["artifact_hash"]) == "approved"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT run_id, artifact_hash, decision FROM approvals"
        ).fetchone()
    assert row == (state["run_id"], state["artifact_hash"], "approved")


def test_stale_approval_is_rejected_after_artifacts_change(monkeypatch, tmp_path):
    _calls, state = _run(monkeypatch, tmp_path)
    database = tmp_path / "team.db"
    original = state["artifact_hash"]
    target = __import__("pathlib").Path(state["workspace_path"]) / "backend" / "main.py"
    target.write_text("print('changed')\n", encoding="utf-8")

    rejected = resume_software_team_workflow(
        state["run_id"],
        approved=True,
        artifact_hash=original,
        db_path=database,
    )

    assert rejected["status"] == "failed"
    assert "Stale approval" in rejected["errors"][0]["message"]
    assert latest_approval(database, state["run_id"], original) is None

    restarted = SqliteCheckpointSaver(str(database))
    snapshot = build_software_team_graph(checkpointer=restarted).get_state(
        {"configurable": {"thread_id": state["run_id"]}}
    )
    assert snapshot.values["status"] == "failed"


def test_restart_cannot_skip_the_approval_gate(monkeypatch, tmp_path):
    _calls, first = _run(monkeypatch, tmp_path)
    assert first["status"] == "awaiting_approval"
    database = tmp_path / "team.db"

    second = workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated-again",
        db_path=database,
        run_id=first["run_id"],
    )

    assert second["status"] != "approved"
    assert latest_approval(database, first["run_id"], first["artifact_hash"]) is None
