"""Static checks for DevOps artifacts.

These checks reject incomplete or inconsistent files. They do not prove that a
Dockerfile will build or that a workflow will succeed.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

import yaml

_DOES_NOT_BUILD = "Static checks do not prove this Dockerfile will build."
_INSTRUCTION = re.compile(
    r"^(?P<name>FROM|RUN|CMD|LABEL|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|VOLUME|USER|WORKDIR|ARG|"
    r"ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL)\b(?P<rest>.*)$",
    re.IGNORECASE,
)
_JSON_FORM = {"CMD", "ENTRYPOINT", "RUN", "COPY", "ADD", "VOLUME", "SHELL"}
_TRIGGERS = {
    "push",
    "pull_request",
    "pull_request_target",
    "workflow_dispatch",
    "schedule",
    "workflow_call",
    "release",
    "workflow_run",
    "merge_group",
}
_BOOL_TAG = "tag:yaml.org,2002:bool"
_SKIP_ARGV = {"python", "python3", "uvicorn", "-m"}


class _OnKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that keeps the GitHub Actions key 'on' as a string."""


_OnKeyLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in patterns if tag != _BOOL_TAG]
    for key, patterns in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_OnKeyLoader.add_implicit_resolver(
    _BOOL_TAG,
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def load_yaml_document(content: str) -> Any:
    """Parse YAML without treating the bare key 'on' as boolean true."""
    return yaml.load(content, Loader=_OnKeyLoader)


def dockerfile_report(content: str) -> tuple[list[str], list[str] | None]:
    """Return Dockerfile issues and a complete runtime command, when one exists."""
    issues: list[str] = []
    has_from = False
    saw_runtime = False
    runtime: list[str] | None = None
    logical = _logical_lines(content)
    if not logical:
        return [f"Dockerfile: file has no instructions. {_DOES_NOT_BUILD}"], None
    for line_number, text in logical:
        match = _INSTRUCTION.match(text)
        if match is None:
            issues.append(
                f"Dockerfile: malformed instruction at line {line_number}: {text!r}. {_DOES_NOT_BUILD}"
            )
            continue
        name = match.group("name").upper()
        rest = match.group("rest").strip()
        if name in _JSON_FORM and "[" in rest:
            array_error = _json_array_error(rest)
            if array_error:
                issues.append(
                    f"Dockerfile: incomplete JSON-array instruction at line {line_number}: "
                    f"{name}. {array_error} {_DOES_NOT_BUILD}"
                )
                continue
        if name == "FROM":
            image = rest.split()[0] if rest else ""
            if not image or image.upper() in {"AS"}:
                issues.append(
                    f"Dockerfile: FROM at line {line_number} has no image. {_DOES_NOT_BUILD}"
                )
            else:
                has_from = True
        if name in {"CMD", "ENTRYPOINT"}:
            saw_runtime = True
            argv = _instruction_argv(rest)
            if argv:
                runtime = argv
            elif "[" not in rest:
                issues.append(
                    f"Dockerfile: {name} at line {line_number} is empty. {_DOES_NOT_BUILD}"
                )
    if not has_from:
        issues.append(f"Dockerfile: missing FROM. {_DOES_NOT_BUILD}")
    if runtime is None and not saw_runtime:
        issues.append(
            f"Dockerfile: missing a complete CMD or ENTRYPOINT. {_DOES_NOT_BUILD}"
        )
    return issues, runtime


def compose_report(path: str, content: str, runtime_argv: list[str] | None) -> list[str]:
    """Validate service structure and SQLite image usage."""
    try:
        document = load_yaml_document(content)
    except yaml.YAMLError as exc:
        return [f"{path}: YAML could not be parsed: {exc}"]
    if not isinstance(document, dict):
        return [f"{path}: document must be a mapping."]
    services = document.get("services")
    if not isinstance(services, dict) or not services:
        return [f"{path}: services must be a non-empty mapping."]

    issues: list[str] = []
    for name, spec in services.items():
        if not isinstance(spec, dict):
            issues.append(f"{path}: service {name!r} must be a mapping.")
            continue
        image = spec.get("image")
        build = spec.get("build")
        if image is None and build is None:
            issues.append(f"{path}: service {name!r} needs an image or a build.")
        if isinstance(image, str) and _sqlite_server_image(image):
            issues.append(
                f"{path}: service {name!r} uses unsupported SQLite server image {image!r}. "
                "SQLite is a file database, not a server image."
            )
        _depends_on_issues(path, name, spec.get("depends_on"), services, issues)
        if build is not None:
            issues.extend(_entrypoint_issues(path, str(name), spec, runtime_argv))
    return issues


def workflow_report(path: str, content: str) -> list[str]:
    """Validate a GitHub Actions workflow, preserving the 'on' trigger key."""
    try:
        document = load_yaml_document(content)
    except yaml.YAMLError as exc:
        return [f"{path}: YAML could not be parsed: {exc}"]
    if not isinstance(document, dict):
        return [f"{path}: workflow must be a mapping."]

    issues: list[str] = []
    if "on" not in document or isinstance(document.get("on"), bool):
        issues.append(
            f"{path}: trigger 'on' is missing or was read as a boolean. "
            "Use a mapping such as push or pull_request."
        )
    elif not _valid_trigger(document.get("on")):
        issues.append(f"{path}: trigger 'on' has no recognizable event.")

    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        issues.append(f"{path}: jobs must be a non-empty mapping.")
        return issues

    recognized_test = False
    for name, job in jobs.items():
        if not isinstance(job, dict):
            issues.append(f"{path}: job {name!r} must be a mapping.")
            continue
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            issues.append(f"{path}: job {name!r} needs a list of steps.")
            continue
        if "test" in str(name).lower():
            recognized_test = True
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                issues.append(f"{path}: job {name!r} step {index} must be a mapping.")
                continue
            if "run" not in step and "uses" not in step:
                issues.append(f"{path}: job {name!r} step {index} needs run or uses.")
            if _runs_tests(step.get("run")):
                recognized_test = True
    if not recognized_test:
        issues.append(f"{path}: workflow has no recognizable test job or pytest step.")
    return issues


def devops_path_issues(paths: list[str]) -> list[str]:
    """Require the three DevOps artifacts, without accepting extra files."""
    names = [path.rsplit("/", 1)[-1] for path in paths]
    dockerfile = [path for path in paths if path.rsplit("/", 1)[-1] == "Dockerfile"]
    compose = [
        path
        for path in paths
        if path.rsplit("/", 1)[-1] in {"docker-compose.yml", "docker-compose.yaml"}
    ]
    workflow = [
        path
        for path in paths
        if path.replace("\\", "/")
        in {".github/workflows/ci.yml", ".github/workflows/ci.yaml"}
    ]
    if len(paths) == 3 and len(dockerfile) == 1 and len(compose) == 1 and len(workflow) == 1:
        return []
    return [
        "DevOps Engineer must return exactly three files: Dockerfile, "
        "docker-compose.yml, and .github/workflows/ci.yml. "
        f"Got {names or paths}."
    ]


def _logical_lines(content: str) -> list[tuple[int, str]]:
    logical: list[tuple[int, str]] = []
    buffer = ""
    start = 1
    for number, raw in enumerate(content.splitlines(), start=1):
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if not buffer:
            start = number
        if stripped.endswith("\\"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        buffer += stripped
        logical.append((start, buffer.strip()))
        buffer = ""
    if buffer.strip():
        logical.append((start, buffer.strip()))
    return logical


def _json_array_error(text: str) -> str:
    start = text.find("[")
    if start < 0:
        return ""
    in_single = False
    in_double = False
    depth = 0
    for character in text[start:]:
        if character == "'" and not in_double:
            in_single = not in_single
            continue
        if character == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single or in_double:
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth < 0:
                return "The JSON array has an extra closing bracket."
    if in_single or in_double or depth != 0:
        return "The JSON array is unclosed."
    return ""


def _instruction_argv(rest: str) -> list[str] | None:
    if not rest:
        return None
    if "[" in rest:
        if _json_array_error(rest):
            return None
        array_text = rest[rest.find("[") :]
        try:
            value = json.loads(array_text)
        except json.JSONDecodeError:
            return None
        if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            return value
        return None
    try:
        argv = shlex.split(rest)
    except ValueError:
        return None
    return argv or None


def _sqlite_server_image(image: str) -> bool:
    name = image.split("@", 1)[0].strip().lower()
    repository = name.split(":", 1)[0]
    leaf = repository.rsplit("/", 1)[-1]
    return leaf in {"sqlite", "sqlite3"}


def _depends_on_issues(path: str, name: object, depends: Any, services: dict, issues: list[str]) -> None:
    if depends is None:
        return
    if isinstance(depends, str):
        targets = [depends]
    elif isinstance(depends, list):
        targets = [str(item) for item in depends]
    elif isinstance(depends, dict):
        targets = [str(item) for item in depends]
    else:
        issues.append(f"{path}: service {name!r} depends_on must be a list or mapping.")
        return
    missing = [item for item in targets if item not in services]
    if missing:
        issues.append(f"{path}: service {name!r} depends on unknown services {missing}.")


def _entrypoint_issues(
    path: str,
    name: str,
    spec: dict,
    runtime_argv: list[str] | None,
) -> list[str]:
    command = spec.get("command", spec.get("entrypoint"))
    if command is None:
        if runtime_argv is None:
            return [
                f"{path}: service {name!r} has no command, and the Dockerfile has no "
                "complete CMD or ENTRYPOINT for the application entrypoint."
            ]
        return []
    argv = command if isinstance(command, list) else None
    if isinstance(command, str):
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = None
    if not argv or not all(isinstance(item, str) for item in argv):
        return [f"{path}: service {name!r} command is not a string or a list of strings."]
    if runtime_argv is None:
        return []
    if _entry_token(argv) != _entry_token(runtime_argv):
        return [
            f"{path}: service {name!r} command {argv} does not match the Dockerfile "
            f"runtime instruction {runtime_argv}."
        ]
    return []


def _entry_token(argv: list[str]) -> str:
    for token in argv:
        if token.endswith(".py") or (":" in token and not token.startswith("-")):
            return token
    remaining = [token for token in argv if token not in _SKIP_ARGV and not token.startswith("-")]
    return " ".join(remaining) if remaining else " ".join(argv)


def _valid_trigger(trigger: Any) -> bool:
    if isinstance(trigger, str):
        return trigger in _TRIGGERS
    if isinstance(trigger, list):
        return bool(trigger) and all(isinstance(item, str) and item in _TRIGGERS for item in trigger)
    if isinstance(trigger, dict):
        return any(str(key) in _TRIGGERS for key in trigger)
    return False


def _runs_tests(command: Any) -> bool:
    if not isinstance(command, str):
        return False
    return "pytest" in command or "unittest" in command
