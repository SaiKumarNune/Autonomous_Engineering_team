"""Bounded patches for files that already exist in a generated workspace.

The repair helper is not one of the seven team roles. Those roles emit a full
artifact set. This helper asks for a small replacement of existing Python files
after a sandbox failure, then validates the patch before it touches disk.
"""

from __future__ import annotations

import difflib
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agents.artifacts import AgentArtifacts, GeneratedFile
from agents.errors import AgentError, ArtifactValidationError
from agents.llm import invoke_agent
from agents.prompt_loader import load_prompt
from agents.structured_output import parse_agent_artifacts
from execution.fingerprint import artifact_hash
from tools.validators import validate_agent_artifacts
from tools.workspace import (
    DEFAULT_MAX_FILE_BYTES,
    WorkspaceSecurityError,
    is_allowed_relative_path,
    list_relative_files,
    read_text,
    resolve_relative_path,
)

MAX_PATCH_FILES = 5
_FORBIDDEN_NAMES = {
    ".env",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "makefile",
    "credentials.py",
    "secrets.py",
    "id_rsa",
}
_FORBIDDEN_PARTS = {".ssh", ".aws", "credentials", "secrets", "sandbox"}


class RepairPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relative_path: str = Field(min_length=1)
    content: str
    language: str = "python"


class RepairProposal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1)
    files: list[RepairPatch] = Field(min_length=1, max_length=MAX_PATCH_FILES)


def request_repair_proposal(context: str) -> RepairProposal:
    raw = invoke_agent("Repair", load_prompt("repair_agent.txt"), context)
    return proposal_from_artifacts(parse_agent_artifacts("Repair", raw))


def proposal_from_artifacts(artifacts: AgentArtifacts) -> RepairProposal:
    return RepairProposal(
        summary=artifacts.summary,
        files=[
            RepairPatch(
                relative_path=item.relative_path,
                content=item.content,
                language=item.language,
            )
            for item in artifacts.files
        ],
    )


def apply_model_repair(state: dict) -> dict:
    """Ask for a patch, validate it, and apply it or restore the previous files."""
    workspace = Path(str(state.get("workspace_path") or ""))
    attempt = int(state.get("repair_attempts") or 0) + 1
    proposal = request_repair_proposal(build_repair_context(state))
    return apply_proposal(workspace, proposal, state, attempt)


def apply_proposal(workspace: Path, proposal: RepairProposal, state: dict, attempt: int) -> dict:
    manifest = manifest_paths(state)
    issues = scope_issues(proposal, manifest)
    if issues:
        raise AgentError("Repair", "Patch scope rejected: " + "; ".join(issues))

    artifacts = AgentArtifacts(
        files=[
            GeneratedFile(
                relative_path=item.relative_path,
                content=item.content,
                language=item.language or "python",
                source_agent="Repair",
            )
            for item in proposal.files
        ],
        summary=proposal.summary,
    )
    patched = {item.relative_path for item in proposal.files}
    validation = validate_agent_artifacts(
        artifacts,
        agent_name="Repair",
        existing_paths=[path for path in manifest if path not in patched],
    )
    if not validation.valid:
        raise ArtifactValidationError("Repair", validation)

    before_hash = artifact_hash(workspace)
    originals = {
        item.relative_path: read_text(workspace, item.relative_path) for item in proposal.files
    }
    if all(originals[item.relative_path] == item.content for item in proposal.files):
        raise AgentError("Repair", "Patch did not change any file.")

    snapshot = snapshot_workspace(workspace, str(state.get("run_id") or "run"), attempt)
    diff = diff_preview(originals, proposal)
    try:
        for item in proposal.files:
            replace_text_atomic(workspace, item.relative_path, item.content)
        after_hash = artifact_hash(workspace)
    except Exception as exc:
        restore_files(workspace, snapshot, list(originals))
        if artifact_hash(workspace) != before_hash:
            raise AgentError("Repair", f"Rollback failed after a patch error: {exc}") from exc
        raise AgentError("Repair", f"Patch was rolled back: {exc}") from exc

    return {
        "attempt": attempt,
        "status": "applied",
        "summary": proposal.summary,
        "files": [item.relative_path for item in proposal.files],
        "diff": diff,
        "artifact_hash_before": before_hash,
        "artifact_hash_after": after_hash,
        "snapshot_path": str(snapshot),
    }


def build_repair_context(state: dict) -> str:
    workspace = Path(str(state.get("workspace_path") or ""))
    results = state.get("test_results") or []
    latest = results[-1] if results else {}
    diagnostics = latest.get("diagnostics") or []
    sections = [
        "SANDBOX DIAGNOSTICS:",
        "\n".join(str(item) for item in diagnostics) or str(latest.get("message") or ""),
        "",
        "STDOUT:",
        str(latest.get("stdout") or "")[:4000],
        "",
        "STDERR:",
        str(latest.get("stderr") or "")[:4000],
        "",
        "EXISTING PYTHON FILES:",
    ]
    for relative in manifest_paths(state):
        if not relative.endswith(".py"):
            continue
        try:
            content = read_text(workspace, relative)
        except (OSError, WorkspaceSecurityError) as exc:
            sections.append(f"{relative}: unreadable ({exc})")
            continue
        sections.append(f"PATH: {relative}\n{content[:8000]}")
    return "\n".join(sections)


def replace_text_atomic(workspace: Path, relative_path: str, content: str) -> None:
    if not isinstance(content, str):
        raise WorkspaceSecurityError(f"{relative_path}: content must be text.")
    data = content.encode("utf-8")
    if len(data) > DEFAULT_MAX_FILE_BYTES:
        raise WorkspaceSecurityError(f"{relative_path}: file exceeds the size limit.")
    if not is_allowed_relative_path(relative_path):
        raise WorkspaceSecurityError(f"{relative_path}: file type is not allowed.")
    target = resolve_relative_path(workspace, relative_path)
    if target.is_symlink() or not target.is_file():
        raise WorkspaceSecurityError(
            f"{relative_path}: repair can only replace an existing regular file."
        )
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=".tmp-",
            suffix=".part",
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.is_symlink():
            raise WorkspaceSecurityError(f"Refusing to replace symbolic link '{relative_path}'.")
        os.replace(temporary_name, target)
        temporary_name = ""
    except WorkspaceSecurityError:
        raise
    except OSError as exc:
        raise WorkspaceSecurityError(
            f"{relative_path}: failed to write file: {exc.strerror or exc}"
        ) from exc
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def scope_issues(proposal: RepairProposal, manifest: list[str]) -> list[str]:
    allowed = {path for path in manifest if path.endswith(".py")}
    issues: list[str] = []
    seen: set[str] = set()
    for item in proposal.files:
        relative = item.relative_path
        name = Path(relative).name.lower()
        parts = {part.lower() for part in Path(relative).parts}
        if relative in seen:
            issues.append(f"{relative}: duplicate patch path.")
        seen.add(relative)
        if not is_allowed_relative_path(relative) or not relative.endswith(".py"):
            issues.append(f"{relative}: repair may only replace an existing Python file.")
            continue
        if name in _FORBIDDEN_NAMES or parts & _FORBIDDEN_PARTS or relative.endswith((".pem", ".key")):
            issues.append(f"{relative}: repair cannot modify secrets or security controls.")
            continue
        if relative not in allowed:
            issues.append(f"{relative}: path is outside the generated Python files for this run.")
    return issues


def manifest_paths(state: dict) -> list[str]:
    paths = []
    for item in state.get("artifact_manifest") or []:
        if isinstance(item, dict) and item.get("relative_path"):
            paths.append(str(item["relative_path"]))
    return paths


def snapshot_workspace(workspace: Path, run_id: str, attempt: int) -> Path:
    destination = workspace.parent / ".repair_snapshots" / str(run_id) / str(attempt)
    if _inside(workspace, destination):
        raise AgentError("Repair", "Refusing to store a snapshot inside the workspace.")
    if destination.exists():
        raise AgentError("Repair", "Repair snapshot already exists for this attempt.")
    destination.mkdir(parents=True)
    for relative in list_relative_files(workspace):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(read_text(workspace, relative), encoding="utf-8")
    return destination


def restore_files(workspace: Path, snapshot: Path, relative_paths: list[str]) -> None:
    for relative in relative_paths:
        content = (snapshot / relative).read_text(encoding="utf-8")
        replace_text_atomic(workspace, relative, content)


def diff_preview(originals: dict[str, str], proposal: RepairProposal) -> str:
    chunks: list[str] = []
    for item in proposal.files:
        previous = originals.get(item.relative_path, "").splitlines(keepends=True)
        updated = item.content.splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                previous,
                updated,
                fromfile=f"a/{item.relative_path}",
                tofile=f"b/{item.relative_path}",
            )
        )
    text = "".join(chunks)
    if len(text) > 8000:
        return text[:8000] + "\n[diff truncated]"
    return text


def _inside(parent: Path, child: Path) -> bool:
    parent_resolved = parent.resolve()
    child_resolved = child if child.exists() else child
    try:
        child_resolved.resolve().relative_to(parent_resolved)
    except ValueError:
        return False
    return True
