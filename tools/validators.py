from typing import Dict, List


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
