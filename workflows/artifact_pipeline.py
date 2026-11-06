"""Validate agent artifacts and write the ones that pass into the run workspace."""

from __future__ import annotations

import re
from pathlib import Path

from agents.artifacts import AgentArtifacts
from agents.errors import AgentError, ArtifactValidationError
from tools.validators import validate_agent_artifacts
from tools.workspace import WorkspaceSecurityError, read_text, resolve_relative_path, write_text_atomic


def heuristic_record(agent_name: str, payload: dict) -> dict[str, object]:
    return {
        "agent": agent_name,
        "valid": bool(payload.get("valid")),
        "issues": [str(issue) for issue in payload.get("issues", [])],
        "blocking": False,
        "kind": "text_heuristics",
        "score": payload.get("score"),
    }


def store_validated_artifacts(
    workspace_path: str,
    agent_name: str,
    artifacts: AgentArtifacts,
    existing_paths: list[str],
) -> dict[str, object]:
    if not isinstance(artifacts, AgentArtifacts):
        raise AgentError(agent_name, "Agent did not return AgentArtifacts.")
    result = validate_agent_artifacts(
        artifacts,
        agent_name=agent_name,
        existing_paths=existing_paths,
    )
    if not result.valid:
        raise ArtifactValidationError(agent_name, result)

    workspace = Path(workspace_path)
    records: list[dict[str, object]] = []
    written: list[str] = []
    try:
        for generated in artifacts.files:
            metadata = write_text_atomic(workspace, generated.relative_path, generated.content)
            relative = str(metadata["relative_path"])
            written.append(relative)
            records.append(
                {
                    "relative_path": relative,
                    "source_agent": agent_name,
                    "language": generated.language,
                    "size_bytes": metadata["size_bytes"],
                    "sha256": metadata["sha256"],
                }
            )
    except WorkspaceSecurityError as exc:
        _remove_written(workspace, written)
        raise AgentError(agent_name, str(exc)) from exc
    except Exception as exc:
        _remove_written(workspace, written)
        raise AgentError(agent_name, f"Failed to write artifacts: {exc}") from exc

    return {
        "summary": artifacts.summary.strip(),
        "records": records,
        "validation": {
            "agent": agent_name,
            "valid": True,
            "issues": [],
            "blocking": True,
            "limitation": result.limitation,
            "kind": "artifact_validation",
        },
    }


_FILE_CHAR_LIMIT = 1200
_TOTAL_CHAR_LIMIT = 6000
_ROUTE = re.compile(
    r"@(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)",
    re.IGNORECASE,
)


def deployment_contract(workspace_path: str, manifest: list[dict]) -> str:
    """Keep routes and the process entrypoint even when file bodies are clipped."""
    workspace = Path(workspace_path)
    routes: list[str] = []
    entrypoint = ""
    database = ""
    for item in manifest:
        relative = str(item.get("relative_path") or "")
        if not relative.endswith(".py"):
            continue
        try:
            content = read_text(workspace, relative)
        except OSError:
            continue
        for match in _ROUTE.finditer(content):
            routes.append(f"{match.group(1).upper()} {match.group(2)} ({relative})")
        if not entrypoint and "uvicorn.run" in content:
            entrypoint = relative
        lowered = content.lower()
        if not database and ("database_url" in lowered or "sqlite" in lowered):
            database = f"SQLite through DATABASE_URL, referenced in {relative}"
    lines = ["API AND DEPLOYMENT CONTRACT:"]
    lines.append(
        f"Entrypoint: {entrypoint or 'backend application file'}. Run with uvicorn."
    )
    lines.append(database or "Database: not stated in the saved Python files.")
    if routes:
        lines.append("Routes:")
        lines.extend(f"- {route}" for route in routes)
    else:
        lines.append("Routes: none found in the saved Python files.")
    lines.append("Dependencies are preinstalled in the sandbox image. Do not run pip.")
    return "\n".join(lines)


def render_stored_files(workspace_path: str, manifest: list[dict], agents: set[str]) -> str:
    """Pass paths and a bounded excerpt. Full files stay in the workspace."""
    workspace = Path(workspace_path)
    chunks: list[str] = []
    for item in manifest:
        if item.get("source_agent") not in agents:
            continue
        relative = str(item.get("relative_path") or "")
        content = read_text(workspace, relative)
        if len(content) > _FILE_CHAR_LIMIT:
            content = content[:_FILE_CHAR_LIMIT] + "\n[truncated for context]\n"
        chunks.append(
            f"PATH: {relative}\n"
            f"LANGUAGE: {item.get('language', '')}\n"
            f"SOURCE: {item.get('source_agent', '')}\n"
            f"CONTENT:\n{content}"
        )
    if not chunks:
        raise AgentError("workflow", "No stored artifacts were available for the next agent.")
    rendered = "\n\n".join(chunks)
    if len(rendered) > _TOTAL_CHAR_LIMIT:
        return rendered[:_TOTAL_CHAR_LIMIT] + "\n[truncated for context]\n"
    return rendered


def _remove_written(workspace: Path, relative_paths: list[str]) -> None:
    for relative in reversed(relative_paths):
        try:
            target = resolve_relative_path(workspace, relative)
        except WorkspaceSecurityError:
            continue
        if target.is_symlink() or not target.is_file():
            continue
        try:
            target.unlink()
        except OSError:
            continue
