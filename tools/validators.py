import ast
import json
from typing import Dict, Iterable, List

from agents.artifacts import AgentArtifacts, ArtifactValidationResult
from tools.devops_validation import (
    compose_report,
    devops_path_issues,
    dockerfile_report,
    workflow_report,
)
from tools.workspace import (
    DEFAULT_MAX_FILE_BYTES,
    expected_language,
    is_allowed_relative_path,
)

_LANGUAGE_ALIASES = {"yml": "yaml", "py": "python"}


def _compute_score(issues: List[str], text: str) -> float:
    if not text or not text.strip():
        return 0.0
    score = 1.0
    deduction = 0.15 * len(issues)
    if len(text.strip()) < 50:
        deduction += 0.2
    return max(0.0, round(score - deduction, 2))


def _find_missing_sections(text: str, required_sections: List[str]) -> List[str]:
    lower_text = text.lower()
    return [section for section in required_sections if section.lower() not in lower_text]


def _find_unsafe_patterns(text: str) -> List[str]:
    patterns = [
        "eval(",
        "exec(",
        "os.system",
        "subprocess.",
        "pickle.loads",
        "input(",
        "open(",
        "__import__(",
    ]
    issues = []
    lower_text = text.lower()
    for pattern in patterns:
        if pattern.lower() in lower_text:
            issues.append(f"unsafe pattern detected: {pattern}")
    return issues


def _find_weak_responses(text: str) -> List[str]:
    weak_phrases = [
        "i'm unable",
        "i cannot",
        "cannot",
        "can't",
        "i don't know",
        "not sure",
        "maybe",
        "as an ai",
        "no idea",
        "i'm sorry",
    ]
    lower_text = text.lower()
    return [phrase for phrase in weak_phrases if phrase in lower_text]


def validate_requirements_output(text: str) -> Dict[str, object]:
    issues: List[str] = []
    if not text or not text.strip():
        issues.append("output is empty")
        return {"valid": False, "issues": issues, "score": 0.0}

    required_sections = [
        "requirements",
        "acceptance criteria",
        "constraints",
        "assumptions",
    ]
    missing = _find_missing_sections(text, required_sections)
    issues.extend([f"missing section: {section}" for section in missing])
    issues.extend(_find_weak_responses(text))
    score = _compute_score(issues, text)
    valid = len(issues) == 0
    return {"valid": valid, "issues": issues, "score": score}


def validate_architecture_output(text: str) -> Dict[str, object]:
    issues: List[str] = []
    if not text or not text.strip():
        issues.append("output is empty")
        return {"valid": False, "issues": issues, "score": 0.0}

    required_sections = [
        "architecture",
        "components",
        "interfaces",
        "data flow",
        "design decisions",
    ]
    missing = _find_missing_sections(text, required_sections)
    issues.extend([f"missing section: {section}" for section in missing])
    issues.extend(_find_weak_responses(text))
    issues.extend(_find_unsafe_patterns(text))
    score = _compute_score(issues, text)
    valid = len(issues) == 0
    return {"valid": valid, "issues": issues, "score": score}


def validate_generated_code(text: str) -> Dict[str, object]:
    issues: List[str] = []
    if not text or not text.strip():
        issues.append("generated code is empty")
        return {"valid": False, "issues": issues, "score": 0.0}

    code_indicators = ["def ", "class ", "import ", "return ", "package ", "func "]
    if not any(indicator in text for indicator in code_indicators):
        issues.append("output does not appear to contain code")

    issues.extend(_find_unsafe_patterns(text))
    issues.extend(_find_weak_responses(text))
    if "TODO" in text or "FIXME" in text:
        issues.append("generated code contains TODO/FIXME markers")

    score = _compute_score(issues, text)
    valid = len(issues) == 0
    return {"valid": valid, "issues": issues, "score": score}


def validate_review_output(text: str) -> Dict[str, object]:
    issues: List[str] = []
    if not text or not text.strip():
        issues.append("review output is empty")
        return {"valid": False, "issues": issues, "score": 0.0}

    required_sections = [
        "summary",
        "issues",
        "recommendations",
        "risks",
    ]
    missing = _find_missing_sections(text, required_sections)
    issues.extend([f"missing section: {section}" for section in missing])
    issues.extend(_find_weak_responses(text))
    score = _compute_score(issues, text)
    valid = len(issues) == 0
    return {"valid": valid, "issues": issues, "score": score}


REQUIRED_ARTIFACTS = {
    "Backend Engineer": "at least one .py file",
    "Frontend Engineer": "a Python UI file when a user interface is requested",
    "QA Engineer": "at least one Python file whose name contains 'test'",
    "DevOps Engineer": "Dockerfile, docker-compose.yml, and .github/workflows/ci.yml",
}


def validate_agent_artifacts(
    artifacts: AgentArtifacts,
    *,
    agent_name: str,
    existing_paths: Iterable[str] = (),
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> ArtifactValidationResult:
    """Validate structure, paths, and syntax before any file is written.

    A valid result means the files satisfy these checks. It does not mean the
    generated program is correct.
    """
    issues: list[str] = []
    if not isinstance(artifacts, AgentArtifacts):
        issues.append("artifacts must be an AgentArtifacts object with files and summary.")
        return ArtifactValidationResult(valid=False, issues=issues)
    if not artifacts.summary.strip():
        issues.append("summary cannot be empty.")

    seen = {path for path in existing_paths}
    paths: list[str] = []
    contents: dict[str, str] = {}
    for index, generated in enumerate(artifacts.files):
        label = generated.relative_path or f"files[{index}]"
        if generated.source_agent != agent_name:
            issues.append(
                f"{label}: source_agent must be {agent_name!r}, got {generated.source_agent!r}."
            )
        if not is_allowed_relative_path(generated.relative_path):
            issues.append(
                f"{label}: path is absolute, escapes the workspace, or uses a disallowed extension."
            )
            continue
        relative = generated.relative_path
        if relative in seen:
            issues.append(f"{relative}: duplicate file path.")
        seen.add(relative)
        paths.append(relative)
        if generated.content == "" or not generated.content.strip():
            issues.append(f"{relative}: empty file.")
        encoded_size = len(generated.content.encode("utf-8"))
        if encoded_size > max_bytes:
            issues.append(
                f"{relative}: file is {encoded_size} bytes, which exceeds the {max_bytes} byte limit."
            )
        expected = expected_language(relative)
        generated.language = _canonical_language(generated.language, expected)
        if expected and generated.language.lower() != expected:
            issues.append(
                f"{relative}: language {generated.language!r} does not match the expected {expected!r}."
            )
        contents[relative] = generated.content
        issues.extend(_syntax_issues(relative, generated.content))

    if agent_name == "DevOps Engineer":
        issues.extend(devops_path_issues(paths))
        issues.extend(_devops_content_issues(contents))
    else:
        issues.extend(_missing_required_artifacts(agent_name, paths))
    return ArtifactValidationResult(valid=not issues, issues=issues)


def _canonical_language(language: str, expected: str) -> str:
    """Rewrite yml/py aliases to yaml/python. Leave file contents unchanged."""
    mapped = _LANGUAGE_ALIASES.get(language.strip().lower(), language.strip().lower())
    if expected and mapped == expected:
        return expected
    return language


def _devops_content_issues(contents: dict[str, str]) -> list[str]:
    issues: list[str] = []
    runtime: list[str] | None = None
    dockerfile = contents.get("Dockerfile")
    if dockerfile is not None:
        dockerfile_issues, runtime = dockerfile_report(dockerfile)
        issues.extend(dockerfile_issues)
    for path, content in contents.items():
        name = path.rsplit("/", 1)[-1]
        if name in {"docker-compose.yml", "docker-compose.yaml"}:
            issues.extend(compose_report(path, content, runtime))
        if path.replace("\\", "/") in {".github/workflows/ci.yml", ".github/workflows/ci.yaml"}:
            issues.extend(workflow_report(path, content))
    return issues


def _missing_required_artifacts(agent_name: str, paths: list[str]) -> list[str]:
    requirement = REQUIRED_ARTIFACTS.get(agent_name)
    if requirement is None:
        return []
    if agent_name == "Frontend Engineer" and not paths:
        return []
    names = [path.rsplit("/", 1)[-1] for path in paths]
    if agent_name in {"Backend Engineer", "Frontend Engineer"}:
        present = any(name.endswith(".py") for name in names)
    elif agent_name == "QA Engineer":
        present = any(name.endswith(".py") and "test" in name.lower() for name in names)
    else:
        present = any(
            name in {"Dockerfile", "docker-compose.yml", "docker-compose.yaml"}
            or name.endswith((".yml", ".yaml"))
            for name in names
        )
    if present:
        return []
    return [f"Missing required project artifact for {agent_name}: {requirement}."]


def _syntax_issues(relative_path: str, content: str) -> list[str]:
    filename = relative_path.rsplit("/", 1)[-1]
    suffix = f".{filename.rsplit('.', 1)[-1]}" if "." in filename else ""
    if suffix == ".py":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            line = exc.lineno or 1
            return [
                f"{relative_path}: Python syntax error at line {line}: {exc.msg}. "
                "ast.parse does not prove the code is correct."
            ]
    if suffix == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return [
                f"{relative_path}: JSON syntax error at line {exc.lineno}: {exc.msg}. "
                "json.loads does not prove the document is correct."
            ]
    return []
