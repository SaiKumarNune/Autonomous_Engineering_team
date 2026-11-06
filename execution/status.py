"""Labels the dashboard can show without treating an untested project as tested."""

from __future__ import annotations

from execution.policy import ALLOWED_PYTEST_COMMAND

NOT_TESTED = "This project has not completed a sandbox test run."
TESTED_PENDING = (
    "Sandbox tests passed. Human approval is required before this artifact version is verified."
)
APPROVED = "This artifact version is approved and sandbox-tested."
STALE = "Stale approval rejected. The artifact hash does not match the approved version."


def sandbox_passed(state: dict) -> bool:
    results = [item for item in state.get("test_results") or [] if isinstance(item, dict)]
    if not results or not state.get("tests_passed"):
        return False
    last = results[-1]
    command = tuple(last.get("command") or [])
    return (
        last.get("kind") == "passed"
        and last.get("sandbox_enforced") is True
        and last.get("exit_code") == 0
        and command == ALLOWED_PYTEST_COMMAND
    )


def describe_verification(state: dict, *, approval_matches: bool | None = None) -> dict[str, object]:
    passed = sandbox_passed(state)
    status = state.get("status")
    if status == "approved" and passed and approval_matches is False:
        label = "stale-approval"
        detail = STALE
    elif status == "approved" and passed and approval_matches is not False:
        label = "approved"
        detail = APPROVED
    elif passed:
        label = "sandbox-tested"
        detail = TESTED_PENDING
    else:
        label = "not-sandbox-tested"
        detail = NOT_TESTED
    return {"label": label, "detail": detail, "sandbox_passed": passed}


def export_allowed(state: dict, *, approval_matches: bool) -> bool:
    return (
        state.get("status") == "approved"
        and state.get("lifecycle_status") == "approved"
        and sandbox_passed(state)
        and approval_matches
    )
