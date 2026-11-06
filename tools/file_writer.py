from __future__ import annotations

from pathlib import Path

from tools.workspace import (
    WorkspaceSecurityError,
    default_workspace_root,
    list_relative_files,
    read_text,
    write_text_atomic,
)

__all__ = ["write_file", "read_file", "list_project_files", "WorkspaceSecurityError"]


def write_file(
    project_name: str,
    file_path: str,
    content: str,
    *,
    root: Path | None = None,
    max_bytes: int | None = None,
) -> str:
    workspace = _workspace(project_name, root)
    kwargs = {"max_bytes": max_bytes} if max_bytes is not None else {}
    write_text_atomic(workspace, file_path, content, **kwargs)
    return str(workspace / Path(file_path))


def read_file(project_name: str, file_path: str, *, root: Path | None = None) -> str:
    return read_text(_workspace(project_name, root), file_path)


def list_project_files(project_name: str, *, root: Path | None = None) -> list[str]:
    workspace = (root or default_workspace_root()) / project_name
    if workspace.is_symlink() or not workspace.is_dir():
        return []
    return list_relative_files(workspace)


def _workspace(project_name: str, root: Path | None) -> Path:
    if not project_name or not str(project_name).strip():
        raise WorkspaceSecurityError("project_name must be a non-empty string.")
    if Path(project_name).name != project_name or project_name in {".", ".."}:
        raise WorkspaceSecurityError("project_name must be a single directory name.")
    base = root or default_workspace_root()
    workspace = base / project_name
    if workspace.is_symlink():
        raise WorkspaceSecurityError("Project workspace cannot be a symbolic link.")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace
