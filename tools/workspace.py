"""Isolated run workspaces for generated project files.

Paths are resolved component by component and symbolic links are never followed.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_FILE_BYTES = 100_000
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")

ALLOWED_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".html",
    ".css",
    ".js",
    ".svg",
    ".xml",
}

ALLOWED_FILENAMES = {
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "requirements.txt",
    ".gitignore",
    ".env.example",
    "Makefile",
    "README.md",
}

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".md": "markdown",
    ".txt": "text",
    ".toml": "toml",
    ".html": "html",
    ".css": "css",
    ".js": "javascript",
    ".xml": "xml",
    ".svg": "svg",
    ".ini": "text",
    ".cfg": "text",
}

LANGUAGE_BY_FILENAME = {
    "Dockerfile": "dockerfile",
    "docker-compose.yml": "yaml",
    "docker-compose.yaml": "yaml",
    "requirements.txt": "text",
    ".gitignore": "text",
    ".env.example": "text",
    "Makefile": "text",
    "README.md": "markdown",
}


class WorkspaceSecurityError(ValueError):
    """A generated path or write would leave the run workspace."""


def default_workspace_root() -> Path:
    configured = os.getenv("PROJECT_OUTPUT_DIR", "").strip()
    if not configured:
        return REPO_ROOT / "generated_projects"
    path = Path(configured)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise WorkspaceSecurityError(
            "run_id must be 8 to 64 characters and contain only letters, numbers, '_' or '-'."
        )
    return run_id


def create_run_workspace(run_id: str, root: Path | None = None) -> Path:
    """Create generated_projects/{run_id}/ or {root}/{run_id}/."""
    safe_id = validate_run_id(run_id)
    base = _anchor_directory(root or default_workspace_root())
    workspace = base / safe_id
    if workspace.exists() or workspace.is_symlink():
        raise WorkspaceSecurityError(f"Run workspace already exists: {safe_id}")
    workspace.mkdir()
    if workspace.is_symlink() or not workspace.is_dir():
        raise WorkspaceSecurityError("Run workspace was not created as a real directory.")
    return workspace


def expected_language(relative_path: str) -> str:
    name = Path(relative_path).name
    if name in LANGUAGE_BY_FILENAME:
        return LANGUAGE_BY_FILENAME[name]
    return LANGUAGE_BY_SUFFIX.get(Path(name).suffix.lower(), "")


def is_allowed_relative_path(relative_path: str) -> bool:
    try:
        _normalize_relative(relative_path)
    except WorkspaceSecurityError:
        return False
    name = Path(relative_path).name
    if name == ".env":
        return False
    if name in ALLOWED_FILENAMES:
        return True
    suffix = Path(name).suffix.lower()
    return suffix in ALLOWED_EXTENSIONS


def resolve_relative_path(workspace: Path, relative_path: str) -> Path:
    """Resolve a relative path and reject traversal or symlink escapes."""
    root = _anchor_directory(workspace)
    relative = _normalize_relative(relative_path)
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise WorkspaceSecurityError(
                f"Refusing to follow symbolic link '{relative_path}'."
            )
    try:
        current.relative_to(root)
    except ValueError as exc:
        raise WorkspaceSecurityError(
            f"Path escapes the workspace: {relative_path}"
        ) from exc
    return current


def write_text_atomic(
    workspace: Path,
    relative_path: str,
    content: str,
    *,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> dict[str, object]:
    if not isinstance(content, str):
        raise WorkspaceSecurityError(f"{relative_path}: content must be text.")
    data = content.encode("utf-8")
    if len(data) > max_bytes:
        raise WorkspaceSecurityError(
            f"{relative_path}: file is {len(data)} bytes, which exceeds the {max_bytes} byte limit."
        )
    if not is_allowed_relative_path(relative_path):
        raise WorkspaceSecurityError(f"{relative_path}: file type is not allowed.")

    target = resolve_relative_path(workspace, relative_path)
    if target.exists() or target.is_symlink():
        raise WorkspaceSecurityError(f"{relative_path}: duplicate file path.")

    _mkdir_parents(workspace, target.parent)
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
            raise WorkspaceSecurityError(
                f"Refusing to replace symbolic link '{relative_path}'."
            )
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

    if target.is_symlink() or not target.is_file():
        raise WorkspaceSecurityError(
            f"{relative_path}: write did not create a regular file inside the workspace."
        )
    return {
        "relative_path": Path(relative_path).as_posix(),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def read_text(workspace: Path, relative_path: str) -> str:
    target = resolve_relative_path(workspace, relative_path)
    if target.is_symlink() or not target.is_file():
        raise WorkspaceSecurityError(f"File is not a regular file: {relative_path}")
    return target.read_text(encoding="utf-8")


def list_relative_files(workspace: Path) -> list[str]:
    root = _anchor_directory(workspace)
    if not root.exists():
        return []
    found: list[str] = []
    _collect_files(root, root, found)
    return sorted(found)


def build_tree(workspace: Path, title: str) -> str:
    files = list_relative_files(workspace)
    tree: dict[str, dict] = {}
    for file_path in files:
        node = tree
        for part in Path(file_path).parts:
            node = node.setdefault(part, {})
    lines = [title]

    def render(node: dict[str, dict], indent: str) -> None:
        for name in sorted(node):
            lines.append(f"{indent}{name}")
            render(node[name], indent + "  ")

    render(tree, "  ")
    return "\n".join(lines)


def build_project_zip(workspace: Path) -> bytes:
    """Zip regular, allowed files. Symlinks and disallowed names are omitted."""
    root = _anchor_directory(workspace)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in list_relative_files(root):
            if not is_allowed_relative_path(relative):
                continue
            target = resolve_relative_path(root, relative)
            if target.is_symlink() or not target.is_file():
                continue
            archive.write(target, arcname=relative)
    return buffer.getvalue()


def _anchor_directory(path: Path) -> Path:
    if path.is_symlink():
        raise WorkspaceSecurityError("Workspace root cannot be a symbolic link.")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise WorkspaceSecurityError("Workspace root is not a real directory.")
    return path


def _normalize_relative(relative_path: str) -> str:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise WorkspaceSecurityError("File path must be a non-empty relative path.")
    if "\x00" in relative_path or "\\" in relative_path:
        raise WorkspaceSecurityError(f"Unsafe file path: {relative_path}")
    candidate = Path(relative_path)
    if candidate.is_absolute() or candidate.drive:
        raise WorkspaceSecurityError(f"Absolute paths are not allowed: {relative_path}")
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise WorkspaceSecurityError(f"Path escapes the workspace: {relative_path}")
    return candidate.as_posix()


def _mkdir_parents(workspace: Path, directory: Path) -> None:
    root = _anchor_directory(workspace)
    relative = directory.relative_to(root)
    current = root
    if str(relative) == ".":
        return
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise WorkspaceSecurityError(f"Refusing to create a directory through symlink '{part}'.")
        if current.exists() and not current.is_dir():
            raise WorkspaceSecurityError(f"Cannot create a directory because '{part}' is a file.")
        current.mkdir(exist_ok=True)


def _collect_files(root: Path, current: Path, found: list[str]) -> None:
    for entry in sorted(current.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            continue
        if entry.is_dir():
            if entry.name in {"__pycache__", ".git", ".venv", "node_modules"}:
                continue
            _collect_files(root, entry, found)
            continue
        if entry.is_file():
            found.append(entry.relative_to(root).as_posix())
