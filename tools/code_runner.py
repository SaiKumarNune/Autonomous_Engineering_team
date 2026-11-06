from pathlib import Path


def _load_project_output_dir() -> Path:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        raise FileNotFoundError(f".env file not found at {env_path}")

    env_values = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        env_values[key.strip()] = value.strip().strip('"').strip("'")

    output_dir = env_values.get("PROJECT_OUTPUT_DIR")
    if not output_dir:
        raise KeyError("PROJECT_OUTPUT_DIR not defined in .env")

    output_dir_path = Path(output_dir)
    if not output_dir_path.is_absolute():
        output_dir_path = (env_path.parent / output_dir_path).resolve()

    return output_dir_path


def _validate_project_name(project_name: str) -> Path:
    if not project_name or Path(project_name).name != project_name:
        raise ValueError("project_name must be a single relative project directory name")

    project_path = Path(project_name)
    if project_path.is_absolute() or any(part == ".." for part in project_path.parts):
        raise ValueError("project_name must be a relative project directory name")

    project_root = _load_project_output_dir() / project_path
    if project_root.is_symlink():
        raise ValueError("Refusing to use a symlinked project directory.")
    return project_root


def _error(command: list, message: str) -> dict:
    return {
        "status": "error",
        "command": command,
        "stdout": "",
        "stderr": message,
        "returncode": None,
        "sandbox_enforced": False,
    }


def run_pytest(project_name: str) -> dict:
    """Run pytest inside the sandbox. This never executes generated code on the host."""
    from execution.sandbox import execute_pytest

    try:
        project_root = _validate_project_name(project_name)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        return _error(["pytest"], str(exc))
    if project_root.is_symlink() or not project_root.is_dir():
        return _error(["pytest"], f"Project directory not found: {project_root}")
    return execute_pytest(project_root, project_root.name).as_legacy_dict()


def run_python_file(project_name: str, file_path: str) -> dict:
    """Refuse host execution. Only the allowlisted pytest command runs in the sandbox."""
    return _error(
        ["python", file_path],
        "Refusing to execute generated Python on the host. "
        "Only the allowlisted pytest command runs inside the sandbox.",
    )
