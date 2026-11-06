"""Human approval bound to one run and one artifact hash."""

from __future__ import annotations

from pathlib import Path

from database.repository import latest_approval, record_approval
from execution.fingerprint import artifact_hash
from execution.status import sandbox_passed


def apply_approval_decision(state: dict, decision: object) -> dict:
    """Accept a resume payload only when it matches the current artifact bytes."""
    if not isinstance(decision, dict):
        return _rejected(state, "Approval decision must be an object.")

    run_id = str(state.get("run_id") or "")
    expected = str(state.get("artifact_hash") or "")
    supplied_run = str(decision.get("run_id") or "")
    supplied_hash = str(decision.get("artifact_hash") or "")
    workspace = str(state.get("workspace_path") or "")
    try:
        current = artifact_hash(Path(workspace)) if workspace else ""
    except OSError as exc:
        return _rejected(state, f"Unable to hash the workspace: {exc}")

    if supplied_run != run_id or not run_id:
        return _rejected(state, "Approval run id does not match this workflow.")
    if not expected or supplied_hash != expected or current != expected:
        return _rejected(
            state,
            "Stale approval rejected because the artifact hash changed.",
        )
    if not state.get("tests_passed") or not sandbox_passed(state):
        return _rejected(state, "Approval rejected because sandbox tests have not passed.")

    database = state.get("db_path")
    approved = decision.get("approved") is True
    try:
        record_approval(database, run_id, current, "approved" if approved else "rejected")
    except Exception as exc:
        return _rejected(state, f"Unable to persist the approval decision: {exc}")

    if not approved:
        return _rejected(state, "Human rejected this artifact version.")
    if latest_approval(database, run_id, current) != "approved":
        return _rejected(state, "Approval was not stored for this artifact hash.")
    return {
        "status": "approved",
        "lifecycle_status": "approved",
        "artifact_hash": current,
    }


def _rejected(state: dict, message: str) -> dict:
    return {
        "status": "failed",
        "lifecycle_status": "failed",
        "artifact_hash": state.get("artifact_hash", ""),
        "errors": [{"agent": "approval", "message": message}],
    }
