def _presence_score(item):
    if item is None:
        return 0.0
    if isinstance(item, bool):
        return 1.0 if item else 0.0
    if isinstance(item, (list, dict, str, set, tuple)):
        return 1.0 if len(item) else 0.0
    return 1.0


def _normalize_score(value):
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, score))


def evaluate_workflow_output(state: dict) -> dict:
    """Evaluate a completed workflow output and return metric scores and notes."""
    if not isinstance(state, dict):
        raise ValueError("state must be a dictionary")

    notes = []
    scores = {}

    requirements = state.get("requirements")
    if isinstance(requirements, dict):
        complete_count = 0
        total = 0
        for item in requirements.values():
            if isinstance(item, dict) and item.get("status") in {"done", "complete", "completed", "fulfilled"}:
                complete_count += 1
            elif item in {"done", "complete", "completed", "fulfilled"}:
                complete_count += 1
            total += 1
        requirements_completeness = (complete_count / total) if total else _presence_score(requirements)
    elif isinstance(requirements, list):
        completed = [item for item in requirements if isinstance(item, dict) and item.get("status") in {"done", "complete", "completed", "fulfilled"}]
        requirements_completeness = (len(completed) / len(requirements)) if requirements else 0.0
    else:
        requirements_completeness = _presence_score(requirements)

    if requirements_completeness >= 0.9:
        notes.append("Requirements are well covered.")
    elif requirements_completeness >= 0.5:
        notes.append("Requirements are partially covered.")
    else:
        notes.append("Requirements coverage is low.")

    architecture_quality = _normalize_score(state.get("architecture_quality"))
    if architecture_quality is None:
        architecture_quality = 1.0 if state.get("architecture") else 0.0
    if architecture_quality >= 0.9:
        notes.append("Architecture is strong.")
    elif architecture_quality >= 0.5:
        notes.append("Architecture is adequate.")
    else:
        notes.append("Architecture needs improvement.")

    backend_code_present = _presence_score(state.get("backend_code") or state.get("backend") or state.get("server_code"))
    if backend_code_present:
        notes.append("Backend code is present.")
    else:
        notes.append("Backend code is missing.")

    frontend_code_present = _presence_score(state.get("frontend_code") or state.get("frontend") or state.get("client_code"))
    if frontend_code_present:
        notes.append("Frontend code is present.")
    else:
        notes.append("Frontend code is missing.")

    test_coverage_present = _presence_score(state.get("tests") or state.get("test_coverage") or state.get("coverage"))
    if test_coverage_present:
        notes.append("Test coverage is present.")
    else:
        notes.append("Test coverage information is missing.")

    devops_present = _presence_score(state.get("devops") or state.get("ci_cd") or state.get("deployment") or state.get("infrastructure"))
    if devops_present:
        notes.append("DevOps artifacts are included.")
    else:
        notes.append("DevOps artifacts are missing.")

    reviewer_feedback_present = _presence_score(state.get("reviewer_feedback") or state.get("feedback") or state.get("review_comments"))
    if reviewer_feedback_present:
        notes.append("Reviewer feedback is available.")
    else:
        notes.append("Reviewer feedback is missing.")

    overall_score = (
        requirements_completeness
        + architecture_quality
        + backend_code_present
        + frontend_code_present
        + test_coverage_present
        + devops_present
        + reviewer_feedback_present
    ) / 7.0

    scores.update(
        requirements_completeness=requirements_completeness,
        architecture_quality=architecture_quality,
        backend_code_present=backend_code_present,
        frontend_code_present=frontend_code_present,
        test_coverage_present=test_coverage_present,
        devops_present=devops_present,
        reviewer_feedback_present=reviewer_feedback_present,
        overall_score=overall_score,
    )

    return {"scores": scores, "notes": notes}
