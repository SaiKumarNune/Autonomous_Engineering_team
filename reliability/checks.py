"""Checkpoint and workspace checks. These do not approve a run or execute generated code."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from database.checkpoint import CheckpointIntegrityError, SqliteCheckpointSaver, thaw
from execution.fingerprint import artifact_hash
from tools.workspace import WorkspaceSecurityError, read_text


def validate_checkpoint(db_path: str) -> dict[str, object]:
    path = Path(db_path)
    if not path.is_file():
        return {"valid": True, "present": False, "issues": []}
    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT payload FROM graph_checkpoints WHERE id = 1"
            ).fetchone()
    except sqlite3.OperationalError:
        return {"valid": True, "present": False, "issues": []}
    if row is None:
        return {"valid": True, "present": False, "issues": []}
    try:
        payload = json.loads(row[0])
        data = thaw(payload)
        if not isinstance(data, dict) or "storage" not in data or "writes" not in data or "blobs" not in data:
            return {
                "valid": False,
                "present": True,
                "issues": ["Checkpoint payload is missing storage, writes, or blobs."],
            }
        SqliteCheckpointSaver(db_path)
    except CheckpointIntegrityError as exc:
        return {"valid": False, "present": True, "issues": [str(exc)]}
    except Exception as exc:
        return {"valid": False, "present": True, "issues": [f"Checkpoint payload is invalid: {exc}"]}
    return {"valid": True, "present": True, "issues": []}


def verify_artifact_consistency(state: dict) -> dict[str, object]:
    """Compare the manifest and approval hash with the files on disk."""
    issues: list[str] = []
    drift: list[str] = []
    workspace = str(state.get("workspace_path") or "")
    root = Path(workspace) if workspace else None
    manifest = state.get("artifact_manifest") or []
    if manifest and (root is None or not root.is_dir()):
        issues.append("Workspace directory is missing.")
        root = None
    if root is not None and root.is_dir():
        for item in manifest:
            if not isinstance(item, dict):
                continue
            relative = str(item.get("relative_path") or "")
            if not relative:
                continue
            try:
                text = read_text(root, relative)
            except (OSError, WorkspaceSecurityError) as exc:
                issues.append(f"{relative}: {exc}")
                continue
            expected = str(item.get("sha256") or "")
            if expected:
                actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if actual != expected:
                    drift.append(relative)
        stored = str(state.get("artifact_hash") or "")
        if stored:
            current = artifact_hash(root)
            if current != stored:
                issues.append("Stored artifact hash does not match the workspace.")
            else:
                drift = []
    return {"consistent": not issues, "issues": issues, "manifest_hash_drift": drift}
