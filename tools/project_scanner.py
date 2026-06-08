from pathlib import Path

IGNORE_DIRS = {"__pycache__", ".git", ".venv", "node_modules"}
BASE_DIR = Path(__file__).resolve().parent.parent / "generated_projects"


def _project_root(project_name: str) -> Path:
    project_root = (BASE_DIR / project_name).resolve()
    base_root = BASE_DIR.resolve()
    if not project_root.exists() or not project_root.is_dir():
        raise FileNotFoundError(f"Project not found: {project_name}")
    if base_root not in project_root.parents and project_root != base_root:
        raise ValueError("Unsafe project path")
    return project_root


def _is_ignored_path(path: Path, root: Path) -> bool:
    for part in path.relative_to(root).parts:
        if part in IGNORE_DIRS:
            return True
    return False


def list_files(project_name: str) -> list[str]:
    root = _project_root(project_name)
    files: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if _is_ignored_path(path, root):
            continue
        files.append(path.relative_to(root).as_posix())
    return sorted(files)


def read_project_file(project_name: str, file_path: str) -> str:
    root = _project_root(project_name)
    candidate = Path(file_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Unsafe file path")
    file_path_resolved = (root / candidate).resolve()
    if root not in file_path_resolved.parents and file_path_resolved != root:
        raise ValueError("Unsafe file path")
    if not file_path_resolved.exists() or not file_path_resolved.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    if _is_ignored_path(file_path_resolved, root):
        raise FileNotFoundError(f"File not found: {file_path}")
    return file_path_resolved.read_text()


def get_project_tree(project_name: str) -> str:
    files = list_files(project_name)
    tree: dict[str, dict] = {}

    for file_path in files:
        node = tree
        for part in Path(file_path).parts:
            node = node.setdefault(part, {})

    lines = [project_name]

    def render(node: dict[str, dict], indent: str) -> None:
        for name in sorted(node):
            lines.append(f"{indent}{name}")
            render(node[name], indent + "  ")

    render(tree, "  ")
    return "\n".join(lines)
