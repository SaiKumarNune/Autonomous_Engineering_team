"""Deterministic FastAPI + SQLite checks shared by Backend, QA, and sandbox gating.

These checks reject a handoff before Docker starts. They do not prove the
application is correct.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from agents.artifacts import AgentArtifacts
from execution.dependencies import allowlist, classify_requirement_lines, inspect_requirements
from tools.workspace import WorkspaceSecurityError, read_text

_REQUIRED_PINS = ("fastapi", "uvicorn", "pytest")


def normalize_backend_requirements(artifacts: AgentArtifacts) -> None:
    """Move a single nested requirements file to the project root."""
    paths = {item.relative_path for item in artifacts.files}
    if "requirements.txt" in paths:
        return
    nested = [item for item in artifacts.files if item.relative_path.endswith("/requirements.txt")]
    if len(nested) == 1:
        nested[0].relative_path = "requirements.txt"


def backend_contract_issues(artifacts: AgentArtifacts) -> list[str]:
    """Require one FastAPI entrypoint, SQLite, and exact root pins."""
    issues: list[str] = []
    python_files = [item for item in artifacts.files if item.relative_path.endswith(".py")]
    entrypoints: list[str] = []
    uses_sqlite = False
    imported: set[str] = set()
    parsed: list[tuple[str, ast.AST]] = []
    for item in python_files:
        try:
            tree = ast.parse(item.content)
        except SyntaxError as exc:
            line = exc.lineno or 1
            issues.append(f"{item.relative_path}: Python syntax error at line {line}: {exc.msg}.")
            continue
        parsed.append((item.relative_path, tree))
        names = _fastapi_names(tree)
        if len(names) > 1:
            issues.append(f"ambiguous FastAPI entrypoint in {item.relative_path}: {', '.join(names)}.")
        elif len(names) == 1:
            entrypoints.append(item.relative_path)
        if _uses_sqlite(tree):
            uses_sqlite = True
        imported.update(_imported_roots(tree))
    orm_models = _orm_models(parsed)
    for path, tree in parsed:
        issues.extend(_orm_annotation_issues(path, tree, orm_models))
    if not entrypoints and not any("Python syntax error" in issue for issue in issues):
        issues.append("missing FastAPI entrypoint.")
    elif len(entrypoints) > 1:
        issues.append("ambiguous FastAPI entrypoints: " + ", ".join(entrypoints) + ".")
    if python_files and not uses_sqlite and not any("Python syntax error" in issue for issue in issues):
        issues.append("missing SQLite configuration.")
    requirements = next((item for item in artifacts.files if item.relative_path == "requirements.txt"), None)
    if requirements is None:
        issues.append("missing requirements.txt.")
    else:
        report = classify_requirement_lines(requirements.content)
        approved = {str(row).split("==", 1)[0].lower() for row in report["approved"]}
        issues.extend(f"unapproved dependency {row}." for row in report["unapproved"])
        issues.extend(
            f"unpinned or mismatched dependency {row}." for row in report["unpinned_or_mismatched"]
        )
        allowed = allowlist()
        for name in (*_REQUIRED_PINS, *(root for root in imported if root in allowed)):
            if name not in approved:
                issues.append(f"requirements.txt does not pin {name}=={allowed[name]}.")
    return issues


def qa_contract_issues(artifacts: AgentArtifacts, local_modules: set[str] | None = None) -> list[str]:
    """Require collectable pytest tests and approved imports."""
    issues: list[str] = []
    local = set(local_modules or set())
    local.update(_local_names(item.relative_path for item in artifacts.files))
    collectable = False
    for item in artifacts.files:
        if not item.relative_path.endswith(".py"):
            continue
        try:
            tree = ast.parse(item.content)
        except SyntaxError as exc:
            line = exc.lineno or 1
            issues.append(f"{item.relative_path}: Python syntax error at line {line}: {exc.msg}.")
            continue
        name = item.relative_path.rsplit("/", 1)[-1]
        if _is_test_file(name) and _has_test_function(tree):
            collectable = True
        for root in _imported_roots(tree):
            if root in sys.stdlib_module_names or root in local or root in allowlist() or root == "__future__":
                continue
            issues.append(f"unsupported dependency {root} imported by {item.relative_path}.")
    if not collectable and not any("Python syntax error" in issue for issue in issues):
        issues.append("missing a collectable pytest test.")
    return issues


def sandbox_dependency_block(workspace: str | Path) -> tuple[str | None, dict[str, object]]:
    """Return a failure message when dependencies are unsafe to execute."""
    root = Path(workspace)
    inspection = inspect_requirements(root)
    problems: list[str] = []
    if not inspection["requirements_present"]:
        problems.append("requirements.txt is missing")
    problems.extend(f"unapproved dependency {item}" for item in inspection["unapproved"])
    problems.extend(
        f"unpinned or mismatched dependency {item}" for item in inspection["unpinned_or_mismatched"]
    )
    if root.is_dir():
        problems.extend(_workspace_import_problems(root))
    if not problems:
        return None, inspection
    message = "Unsupported dependencies: " + "; ".join(problems) + ". Sandbox execution was not started."
    return message, inspection


def _workspace_import_problems(workspace: Path) -> list[str]:
    files: list[tuple[str, str]] = []
    for path in sorted(workspace.rglob("*.py")):
        if "__pycache__" in path.parts or path.is_symlink():
            continue
        relative = path.relative_to(workspace).as_posix()
        try:
            files.append((relative, read_text(workspace, relative)))
        except (OSError, WorkspaceSecurityError):
            continue
    local = _local_names(relative for relative, _content in files)
    problems: list[str] = []
    allowed = allowlist()
    for relative, content in files:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for root in _imported_roots(tree):
            if root in sys.stdlib_module_names or root in local or root in allowed or root == "__future__":
                continue
            problems.append(f"unsupported dependency {root} imported by {relative}")
    return problems


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


_ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}


def _orm_models(parsed: list[tuple[str, ast.AST]]) -> dict[str, set[str]]:
    models: dict[str, set[str]] = {}
    for path, tree in parsed:
        declarative = {
            target.id
            for node in getattr(tree, "body", [])
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and _calls_declarative_base(node.value)
        }
        models[path] = {
            node.name
            for node in getattr(tree, "body", [])
            if isinstance(node, ast.ClassDef) and _is_orm_class(node, declarative)
        }
    return models


def _orm_annotation_issues(path: str, tree: ast.AST, orm_models: dict[str, set[str]]) -> list[str]:
    """Report SQLAlchemy models used where FastAPI expects a Pydantic field."""
    visible = set(orm_models.get(path, set()))
    visible.update(_imported_orm_names(path, tree, orm_models))
    if not visible:
        return []
    issues: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _route_decorators(node):
            continue
        for decorator in _route_decorators(node):
            if not isinstance(decorator, ast.Call):
                continue
            for keyword in decorator.keywords:
                if keyword.arg != "response_model":
                    continue
                for name in _annotation_names(keyword.value):
                    if name in visible:
                        issues.append(
                            f"{path}:{keyword.lineno}: {node.name} response_model uses "
                            f"SQLAlchemy model {name}. Use a Pydantic schema."
                        )
        if node.returns is not None:
            for name in _annotation_names(node.returns):
                if name in visible:
                    issues.append(
                        f"{path}:{node.returns.lineno}: {node.name} return annotation uses "
                        f"SQLAlchemy model {name}. Use a Pydantic schema."
                    )
        defaults = _argument_defaults(node)
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if arg.annotation is None or _is_depends(defaults.get(arg.arg)):
                continue
            for name in _annotation_names(arg.annotation):
                if name in visible:
                    issues.append(
                        f"{path}:{arg.lineno}: {node.name} parameter '{arg.arg}' uses "
                        f"SQLAlchemy model {name}. Use a Pydantic schema."
                    )
    return issues


def _imported_orm_names(path: str, tree: ast.AST, orm_models: dict[str, set[str]]) -> set[str]:
    names: set[str] = set()
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        source = _module_file(path, node.module, node.level)
        exported = orm_models.get(source or "", set())
        for alias in node.names:
            if alias.name in exported:
                names.add(alias.asname or alias.name)
    return names


def _module_file(path: str, module: str, level: int) -> str | None:
    parent = Path(path).parent
    for _step in range(max(level - 1, 0)):
        parent = parent.parent
    relative = Path(module.replace(".", "/"))
    if level:
        candidate = parent / relative
    else:
        candidate = relative
    return f"{candidate.as_posix()}.py"


def _is_orm_class(node: ast.ClassDef, declarative: set[str]) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and (base.id in declarative or base.id == "DeclarativeBase"):
            return True
        if isinstance(base, ast.Attribute) and base.attr == "DeclarativeBase":
            return True
    return any(
        isinstance(stmt, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__tablename__" for target in stmt.targets)
        for stmt in node.body
    )


def _calls_declarative_base(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "declarative_base"
    return isinstance(func, ast.Attribute) and func.attr == "declarative_base"


def _route_decorators(node: ast.AST) -> list[ast.expr]:
    found: list[ast.expr] = []
    for decorator in getattr(node, "decorator_list", []):
        call = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(call, ast.Attribute) and call.attr in _ROUTE_METHODS:
            found.append(decorator)
    return found


def _argument_defaults(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, ast.expr]:
    positional = [*node.args.posonlyargs, *node.args.args]
    offset = len(positional) - len(node.args.defaults)
    defaults = {
        positional[offset + index].arg: default
        for index, default in enumerate(node.args.defaults)
    }
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        if default is not None:
            defaults[arg.arg] = default
    return defaults


def _is_depends(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Depends"
    return isinstance(func, ast.Attribute) and func.attr == "Depends"


def _annotation_names(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def _uses_sqlite(tree: ast.AST) -> bool:
    if "sqlite3" in _imported_roots(tree):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "sqlite" in node.value.lower():
            return True
    return False


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _local_names(paths) -> set[str]:
    names: set[str] = set()
    for relative in paths:
        path = Path(str(relative))
        names.add(path.stem)
        names.update(part for part in path.parts[:-1] if part not in {"", "."})
    return names


def _is_test_file(name: str) -> bool:
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py")


def _has_test_function(tree: ast.AST) -> bool:
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        for node in ast.walk(tree)
    )
