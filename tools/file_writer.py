from __future__ import annotations

import os
from pathlib import Path

_ROOT_DIR = Path(__file__).resolve().parents[1]
_ENV_FILE = _ROOT_DIR / ".env"


def _load_env() -> dict[str, str]:
    if not _ENV_FILE.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"\'')
        if key:
            values[key] = value

    return values


def _get_project_output_dir() -> Path:
    output_dir = os.environ.get("PROJECT_OUTPUT_DIR")
    if not output_dir:
        output_dir = _load_env().get("PROJECT_OUTPUT_DIR")

    if not output_dir:
        raise RuntimeError("PROJECT_OUTPUT_DIR is not configured in the environment or .env")

    output_path = Path(output_dir)
    if not output_path.is_absolute():
        output_path = _ROOT_DIR / output_path

    return output_path.resolve()


def _safe_project_path(project_name: str, file_path: str) -> Path:
    if not project_name or not project_name.strip():
        raise ValueError("project_name must be a non-empty string")

    if Path(file_path).is_absolute():
        raise ValueError("file_path must be a relative path")

    project_dir = _get_project_output_dir() / project_name
    final_path = (project_dir / file_path).resolve()

    try:
        final_path.relative_to(project_dir.resolve())
    except ValueError as exc:
        raise ValueError("Invalid file_path: path traversal is not allowed") from exc

    return final_path


def write_file(project_name: str, file_path: str, content: str) -> str:
    final_path = _safe_project_path(project_name, file_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(content, encoding="utf-8")
    return str(final_path)


def read_file(project_name: str, file_path: str) -> str:
    final_path = _safe_project_path(project_name, file_path)
    if not final_path.exists():
        raise FileNotFoundError(f"File not found: {final_path}")
    if final_path.is_dir():
        raise IsADirectoryError(f"Expected file but found directory: {final_path}")
    return final_path.read_text(encoding="utf-8")


def list_project_files(project_name: str) -> list[str]:
    project_dir = _get_project_output_dir() / project_name
    if not project_dir.exists():
        return []

    root = project_dir.resolve()
    return [str(path.relative_to(root).as_posix()) for path in sorted(root.rglob("*")) if path.is_file()]


__all__ = ["write_file", "read_file", "list_project_files"]
