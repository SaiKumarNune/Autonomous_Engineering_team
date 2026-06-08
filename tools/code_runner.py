import subprocess
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
    if not project_name:
        raise ValueError("project_name must be provided")

    project_path = Path(project_name)
    if project_path.is_absolute() or any(part == ".." for part in project_path.parts):
        raise ValueError("project_name must be a relative project directory name")

    project_root = _load_project_output_dir() / project_path
    return project_root.resolve()


def _run_command(command: list, cwd: Path) -> dict:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            text=True,
            shell=False,
        )
        status = "success" if result.returncode == 0 else "failure"
        return {
            "status": status,
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "command": command,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "returncode": None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "command": command,
            "stdout": "",
            "stderr": str(exc),
            "returncode": None,
        }


def run_pytest(project_name: str) -> dict:
    project_root = _validate_project_name(project_name)
    if not project_root.is_dir():
        return {
            "status": "error",
            "command": ["pytest"],
            "stdout": "",
            "stderr": f"Project directory not found: {project_root}",
            "returncode": None,
        }

    return _run_command(["pytest"], project_root)


def run_python_file(project_name: str, file_path: str) -> dict:
    project_root = _validate_project_name(project_name)
    if not project_root.is_dir():
        return {
            "status": "error",
            "command": ["python", file_path],
            "stdout": "",
            "stderr": f"Project directory not found: {project_root}",
            "returncode": None,
        }

    if not file_path:
        return {
            "status": "error",
            "command": ["python", file_path],
            "stdout": "",
            "stderr": "file_path must be provided",
            "returncode": None,
        }

    target_path = Path(file_path)
    if target_path.is_absolute() or any(part == ".." for part in target_path.parts):
        return {
            "status": "error",
            "command": ["python", file_path],
            "stdout": "",
            "stderr": "file_path must be relative and cannot escape the project directory",
            "returncode": None,
        }

    absolute_file_path = (project_root / target_path).resolve()
    if not str(absolute_file_path).startswith(str(project_root)):
        return {
            "status": "error",
            "command": ["python", file_path],
            "stdout": "",
            "stderr": "file_path escapes the project directory",
            "returncode": None,
        }

    if not absolute_file_path.is_file():
        return {
            "status": "error",
            "command": ["python", str(absolute_file_path)],
            "stdout": "",
            "stderr": f"Python file not found: {absolute_file_path}",
            "returncode": None,
        }

    return _run_command(["python", str(absolute_file_path)], project_root)
