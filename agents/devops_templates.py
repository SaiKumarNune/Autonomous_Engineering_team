"""Select and render version-controlled DevOps templates.

The DevOps Agent chooses a template from validated project files. It does not
ask a model to write Docker or CI boilerplate. A rendered file is still only a
static configuration: nothing here proves an image will build.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template

from agents.artifacts import AgentArtifacts, GeneratedFile
from agents.errors import AgentError
from execution.dependencies import allowlist, inspect_requirements
from tools.workspace import read_text

AGENT_NAME = "DevOps Engineer"
TEMPLATE_ID = "fastapi-sqlite"
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "fastapi_sqlite"
DEFAULT_PORT = "8000"
SUPPLIED_DATABASE_URL = "sqlite:///./data/app.db"
TEST_COMMAND = "pytest"
_OTHER_DATABASES = {
    "asyncpg",
    "motor",
    "mysql",
    "psycopg",
    "psycopg2",
    "pymongo",
    "pymysql",
    "redis",
}
_FRAMEWORKS = {
    "django": "Django",
    "flask": "Flask",
    "streamlit": "Streamlit",
}


@dataclass(frozen=True)
class _Entrypoint:
    relative_path: str
    application: str
    module: str

    @property
    def target(self) -> str:
        return f"{self.module}:{self.application}"


@dataclass(frozen=True)
class _Database:
    url: str
    supplied_by_template: bool


def render_fastapi_sqlite(workspace_path: str, manifest: list[dict]) -> AgentArtifacts:
    """Render the FastAPI + SQLite template, or reject the project."""
    workspace = Path(workspace_path)
    sources = _python_sources(workspace, manifest)
    entrypoint = _entrypoint(sources)
    database = _database(sources)
    port = _port(sources)
    _require_pytest_tests(sources)
    pins = _dependency_pins(workspace, manifest, sources)
    dockerfile, compose = _render_pair(entrypoint.target, port, database)
    workflow = _read_template("ci.yml.template")
    if "pytest" not in workflow:
        raise AgentError(AGENT_NAME, "The fastapi-sqlite CI template does not run pytest.")
    summary = _summary(entrypoint, port, database, pins)
    return AgentArtifacts(
        summary=summary,
        files=[
            GeneratedFile(
                relative_path="Dockerfile",
                content=dockerfile,
                language="dockerfile",
                source_agent=AGENT_NAME,
            ),
            GeneratedFile(
                relative_path="docker-compose.yml",
                content=compose,
                language="yaml",
                source_agent=AGENT_NAME,
            ),
            GeneratedFile(
                relative_path=".github/workflows/ci.yml",
                content=workflow,
                language="yaml",
                source_agent=AGENT_NAME,
            ),
        ],
    )


def _python_sources(workspace: Path, manifest: list[dict]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for item in manifest:
        relative = str(item.get("relative_path") or "")
        if not relative.endswith(".py"):
            continue
        try:
            sources[relative] = read_text(workspace, relative)
        except OSError as exc:
            raise AgentError(
                AGENT_NAME,
                f"No supported DevOps template: validated file {relative} could not be read.",
            ) from exc
    if not sources:
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: the validated project has no Python files.",
        )
    return sources


def _entrypoint(sources: dict[str, str]) -> _Entrypoint:
    _reject_other_frameworks(sources)
    found: list[_Entrypoint] = []
    for relative, content in sources.items():
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            raise AgentError(
                AGENT_NAME,
                f"No supported DevOps template: {relative} could not be parsed.",
            ) from exc
        names = _fastapi_names(tree)
        if not names:
            continue
        if len(names) != 1:
            raise AgentError(
                AGENT_NAME,
                "No supported DevOps template: ambiguous FastAPI entrypoints in "
                f"{relative}: {', '.join(names)}.",
            )
        found.append(
            _Entrypoint(relative, names[0], _module_name(relative))
        )
    if not found:
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: missing FastAPI entrypoint.",
        )
    if len(found) != 1:
        paths = ", ".join(item.relative_path for item in found)
        raise AgentError(
            AGENT_NAME,
            f"No supported DevOps template: ambiguous FastAPI entrypoints: {paths}.",
        )
    return found[0]


def _reject_other_frameworks(sources: dict[str, str]) -> None:
    for relative, content in sources.items():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for root in _imported_roots(tree):
            framework = _FRAMEWORKS.get(root)
            if framework is not None:
                raise AgentError(
                    AGENT_NAME,
                    "No supported DevOps template: unsupported framework "
                    f"{framework} imported by {relative}.",
                )


def _fastapi_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in getattr(tree, "body", []):
        value = None
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            value = node.value
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            targets = [node.target]
        if value is None or not _calls_fastapi(value):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
    return names


def _calls_fastapi(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "FastAPI"
    return isinstance(func, ast.Attribute) and func.attr == "FastAPI"


def _module_name(relative_path: str) -> str:
    parts = relative_path.split("/")
    if not parts or not parts[-1].endswith(".py"):
        raise AgentError(
            AGENT_NAME,
            f"No supported DevOps template: {relative_path} is not a Python entrypoint.",
        )
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or any(not part.isidentifier() for part in parts):
        raise AgentError(
            AGENT_NAME,
            f"No supported DevOps template: {relative_path} is not an importable module.",
        )
    return ".".join(parts)


def _database(sources: dict[str, str]) -> _Database | None:
    uses_sqlite = False
    other_database = False
    defaults: list[str | None] = []
    reads_env = False
    for content in sources.values():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        if _imports_other_database(tree) or _other_database_literal(tree):
            other_database = True
        if _uses_sqlite(tree):
            uses_sqlite = True
        env_defaults = _database_url_reads(tree)
        if env_defaults:
            reads_env = True
            defaults.extend(env_defaults)
    if other_database and uses_sqlite:
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: ambiguous database configuration. "
            "SQLite and another database are both declared.",
        )
    if other_database:
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: unsupported database. Only SQLite is supported.",
        )
    if not uses_sqlite and not reads_env:
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: project does not use SQLite.",
        )
    if not uses_sqlite:
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: ambiguous database configuration. "
            "DATABASE_URL is read, but no SQLite URL or sqlite3 import was found.",
        )
    if not reads_env:
        return None
    concrete = [item for item in defaults if item]
    if any(not item.lower().startswith("sqlite:") for item in concrete):
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: DATABASE_URL default is not a SQLite URL.",
        )
    unique = set(concrete)
    if len(unique) > 1:
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: ambiguous DATABASE_URL defaults: "
            + ", ".join(sorted(unique))
            + ".",
        )
    if unique:
        url = next(iter(unique))
        _reject_unsafe_url(url)
        return _Database(url, supplied_by_template=False)
    return _Database(SUPPLIED_DATABASE_URL, supplied_by_template=True)


def _uses_sqlite(tree: ast.AST) -> bool:
    for root in _imported_roots(tree):
        if root == "sqlite3":
            return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "sqlite" in node.value.lower():
                return True
    return False


def _imports_other_database(tree: ast.AST) -> bool:
    return any(root in _OTHER_DATABASES for root in _imported_roots(tree))


def _other_database_literal(tree: ast.AST) -> bool:
    markers = ("postgres://", "postgresql://", "mysql://", "mongodb://", "redis://")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if any(marker in lowered for marker in markers):
                return True
    return False


def _database_url_reads(tree: ast.AST) -> list[str | None]:
    reads: list[str | None] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_database_url_call(node):
            default = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                if isinstance(node.args[1].value, str):
                    default = node.args[1].value
            reads.append(default)
        elif isinstance(node, ast.Subscript) and _slice_is_database_url(node):
            reads.append(None)
    return reads


def _is_database_url_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in {"get", "getenv"}:
        pass
    elif isinstance(func, ast.Name) and func.id == "getenv":
        pass
    else:
        return False
    if not node.args or not isinstance(node.args[0], ast.Constant):
        return False
    return node.args[0].value == "DATABASE_URL"


def _slice_is_database_url(node: ast.Subscript) -> bool:
    slice_node = node.slice
    return isinstance(slice_node, ast.Constant) and slice_node.value == "DATABASE_URL"


def _port(sources: dict[str, str]) -> str:
    ports: set[str] = set()
    for content in sources.values():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_uvicorn_run(node):
                continue
            for keyword in node.keywords:
                if keyword.arg == "port" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, int):
                        ports.add(str(keyword.value.value))
    if len(ports) > 1:
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: ambiguous ports: " + ", ".join(sorted(ports)) + ".",
        )
    port = next(iter(ports), DEFAULT_PORT)
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise AgentError(AGENT_NAME, f"No supported DevOps template: port {port} is invalid.")
    return port


def _is_uvicorn_run(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "run" and (
        (isinstance(func.value, ast.Name) and func.value.id == "uvicorn")
        or (isinstance(func.value, ast.Attribute) and func.value.attr == "uvicorn")
    )


def _require_pytest_tests(sources: dict[str, str]) -> None:
    collectable = False
    for relative, content in sources.items():
        name = relative.rsplit("/", 1)[-1]
        if not (name.startswith("test_") and name.endswith(".py")) and not name.endswith("_test.py"):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            raise AgentError(
                AGENT_NAME,
                f"No supported DevOps template: {relative} could not be parsed.",
            ) from exc
        if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_") for node in ast.walk(tree)):
            collectable = True
    if not collectable:
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: missing a collectable pytest command.",
        )


def _reject_unsafe_url(url: str) -> None:
    if any(character in url for character in "\"'\n\\$"):
        raise AgentError(
            AGENT_NAME,
            "No supported DevOps template: DATABASE_URL cannot be rendered safely.",
        )


def _dependency_pins(workspace: Path, manifest: list[dict], sources: dict[str, str]) -> list[str]:
    report = inspect_requirements(workspace)
    problems: list[str] = []
    listed = {str(item.get("relative_path") or "") for item in manifest}
    if "requirements.txt" not in listed:
        problems.append("requirements.txt is not in the validated project metadata")
    if not report["requirements_present"]:
        problems.append("requirements.txt is missing")
    problems.extend(f"unapproved dependency {item}" for item in report["unapproved"])
    problems.extend(f"unpinned or mismatched dependency {item}" for item in report["unpinned_or_mismatched"])
    approved = {item.split("==", 1)[0].lower() for item in report["approved"]}
    imported: dict[str, str] = {}
    for relative, content in sources.items():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for root in _imported_roots(tree):
            imported.setdefault(root, relative)
    local = _local_modules(sources)
    allowed = allowlist()
    for root, relative in sorted(imported.items()):
        if root in sys.stdlib_module_names or root in local or root == "__future__":
            continue
        if root in _FRAMEWORKS or root in _OTHER_DATABASES:
            continue
        if root not in allowed:
            problems.append(f"unsupported dependency {root} imported by {relative}")
            continue
        if root not in approved:
            problems.append(
                f"requirements.txt does not pin {root}=={allowed[root]}, imported by {relative}"
            )
    for required in ("fastapi", "uvicorn", "pytest"):
        if required not in approved:
            problems.append(
                f"requirements.txt does not pin {required}=={allowed[required]}"
            )
    if problems:
        unique = list(dict.fromkeys(problems))
        raise AgentError(
            AGENT_NAME,
            "Inconsistent dependency declarations: " + "; ".join(unique) + ".",
        )
    return [str(item) for item in report["approved"]]


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _local_modules(sources: dict[str, str]) -> set[str]:
    names: set[str] = set()
    for relative in sources:
        path = Path(relative)
        names.add(path.stem)
        names.update(part for part in path.parts[:-1] if part not in {"", "."})
    return names


def _render_pair(entrypoint: str, port: str, database: _Database | None) -> tuple[str, str]:
    values = {"entrypoint": entrypoint, "port": port}
    if database is None:
        return (
            _render("Dockerfile.template", values),
            _render("docker-compose.yml.template", values),
        )
    values["database_url"] = database.url
    return (
        _render("Dockerfile.database-url.template", values),
        _render("docker-compose.database-url.yml.template", values),
    )


def _render(name: str, values: dict[str, str]) -> str:
    try:
        return Template(_read_template(name)).substitute(values)
    except KeyError as exc:
        raise AgentError(
            AGENT_NAME,
            f"The fastapi-sqlite template {name} is missing {exc.args[0]}.",
        ) from exc


def _read_template(name: str) -> str:
    path = TEMPLATE_DIR / name
    if not path.is_file():
        raise AgentError(AGENT_NAME, f"The fastapi-sqlite template {name} is not available.")
    return path.read_text(encoding="utf-8")


def _summary(entrypoint: _Entrypoint, port: str, database: _Database | None, pins: list[str]) -> str:
    if database is None:
        database_sentence = "The application opens SQLite itself. No database environment variable is set."
    elif database.supplied_by_template:
        database_sentence = (
            f"DATABASE_URL is set to {database.url} because the application reads it and does not define a default."
        )
    else:
        database_sentence = f"DATABASE_URL matches the application default {database.url}."
    port_sentence = (
        f"Port {port} comes from the application."
        if port != DEFAULT_PORT
        else f"Port {port} is the template default because the application did not set one."
    )
    return (
        f"Selected the {TEMPLATE_ID} template. "
        f"Entrypoint is uvicorn {entrypoint.target} from {entrypoint.relative_path}. "
        f"{port_sentence} "
        f"Pinned dependencies: {', '.join(pins)}. "
        f"Tests run with {TEST_COMMAND}. "
        f"{database_sentence} "
        "No SQLite server container is included. "
        "These files are rendered from version-controlled templates, not written by a model. "
        "Static checks do not prove the image will build."
    )
