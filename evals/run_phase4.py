"""Run the Phase 4 benchmark against the configured local Ollama model.

This script does not mock agents, approve artifacts, push, or deploy.
If Ollama or Docker is unavailable it records the blocker and exits.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from database.repository import latest_approval
from evals.benchmark import TASKS, measure_run, summarize_benchmark
from execution.approval import apply_approval_decision
from execution.fingerprint import artifact_hash
from workflows.software_team_graph import run_software_team_workflow

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = REPO_ROOT / "evals" / "phase4_results.json"


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    blocker = _environment_blocker()
    if blocker:
        payload = {"execution": "blocked", "blocker": blocker, "runs": [], "summary": None}
        RESULTS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 2

    database = REPO_ROOT / "database" / "phase4_validation.db"
    workspace_root = REPO_ROOT / "generated_projects"
    stamp = time.strftime("%H%M%S")
    runs = []
    for index, task in enumerate(TASKS, start=1):
        run_id = f"phase4{stamp}t{index}"
        measured = measure_run(
            task,
            lambda request, selected, _run_id=run_id: run_software_team_workflow(
                request,
                workspace_root=workspace_root,
                db_path=database,
                run_id=_run_id,
            ),
            run_id,
        )
        state = _load_state_fields(measured)
        measured.update(state)
        if task["task_id"] == "task-api":
            measured["approval_gate"] = _check_gate_without_approving(database, measured)
        runs.append(measured)
        print(json.dumps({"task_id": measured["task_id"], "status": measured["status"]}, indent=2), flush=True)

    payload = {
        "execution": "ollama",
        "model": os.getenv("OLLAMA_MODEL", ""),
        "approved_automatically": False,
        "summary": summarize_benchmark(runs),
        "runs": runs,
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return 0


def _environment_blocker() -> str:
    if shutil.which("docker") is None:
        return "Docker is not installed."
    info = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    if info.returncode != 0:
        detail = (info.stderr or info.stdout or "docker info failed").strip()
        return f"Docker is not available: {detail[-300:]}"
    image = subprocess.run(
        ["docker", "image", "inspect", "autonomous-team-sandbox:local"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    if image.returncode != 0:
        return "Sandbox image autonomous-team-sandbox:local is not built."
    model = os.getenv("OLLAMA_MODEL", "").strip()
    if not model:
        return "OLLAMA_MODEL is empty."
    listed = subprocess.run(
        ["ollama", "list"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    if listed.returncode != 0:
        detail = (listed.stderr or listed.stdout or "ollama list failed").strip()
        return f"Ollama is not available: {detail[-300:]}"
    names = [line.split()[0] for line in listed.stdout.splitlines()[1:] if line.strip()]
    if model not in names:
        return f"Configured model {model} is not present in the local Ollama catalog."
    return ""


def _load_state_fields(measured: dict) -> dict:
    """The measurement already stores the workflow fields needed for the report."""
    return {
        "model": os.getenv("OLLAMA_MODEL", ""),
    }


def _check_gate_without_approving(database: Path, measured: dict) -> dict:
    """Prove the current hash matches, and that a different hash is refused.

    This does not record an approval. A hash mismatch returns before any
    approval row is written.
    """
    if measured.get("status") != "awaiting_approval":
        return {
            "checked": False,
            "reason": f"Run status is {measured.get('status')}, so the gate was not opened.",
        }
    from workflows.software_team_graph import _checkpoint_state

    state = _checkpoint_state(str(database), measured["run_id"]) or {}
    workspace = Path(str(state.get("workspace_path") or ""))
    current = artifact_hash(workspace) if workspace.is_dir() else ""
    mismatch = apply_approval_decision(
        state,
        {
            "approved": True,
            "run_id": measured["run_id"],
            "artifact_hash": "0" * 64,
        },
    )
    stored = latest_approval(database, measured["run_id"], current)
    return {
        "checked": True,
        "approved": False,
        "current_hash_matches_workspace": current == state.get("artifact_hash") and bool(current),
        "stale_hash_rejected": "Stale approval" in str((mismatch.get("errors") or [{}])[0].get("message")),
        "approval_row_written": stored is not None,
    }


if __name__ == "__main__":
    sys.exit(main())
