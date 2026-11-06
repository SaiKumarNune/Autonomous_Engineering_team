from pathlib import Path

from tools.workspace import (
    WorkspaceSecurityError,
    build_tree,
    default_workspace_root,
    list_relative_files,
    read_text,
)

IGNORE_DIRS = {"__pycache__", ".git", ".venv", "node_modules"}


def _project_root(project_name: str, base_dir: Path | None = None) -> Path:
    if not project_name or Path(project_name).name != project_name or project_name in {".", ".."}:
        raise WorkspaceSecurityError("Unsafe project path")
    root = (base_dir or default_workspace_root()) / project_name
    if root.is_symlink() or not root.is_dir():
        raise FileNotFoundError(f"Project not found: {project_name}")
    return root


def list_files(project_name: str, *, base_dir: Path | None = None) -> list[str]:
    return list_relative_files(_project_root(project_name, base_dir))


def read_project_file(
    project_name: str,
    file_path: str,
    *,
    base_dir: Path | None = None,
) -> str:
    return read_text(_project_root(project_name, base_dir), file_path)


def get_project_tree(project_name: str, *, base_dir: Path | None = None) -> str:
    root = _project_root(project_name, base_dir)
    return build_tree(root, project_name)
