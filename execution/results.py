"""Structured results for one sandbox test run."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from database.repository import redact

Kind = Literal["passed", "failed", "execution_error", "timeout", "sandbox_error"]
_FAILURE_PREFIXES = ("FAILED ", "ERROR ", "E ")


class TestExecutionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Kind
    command: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    sandbox_enforced: bool = False
    failure_signature: str = ""
    diagnostics: list[str] = Field(default_factory=list)
    message: str = ""

    def as_legacy_dict(self) -> dict[str, object]:
        status = "success" if self.kind == "passed" else self.kind
        return {
            "status": status,
            "command": list(self.command),
            "stdout": self.stdout,
            "stderr": self.stderr or self.message,
            "returncode": self.exit_code,
            "sandbox_enforced": self.sandbox_enforced,
        }


def normalize_test_result(payload: object) -> TestExecutionResult:
    """Accept a service payload and refuse a pass that did not come from the sandbox."""
    if isinstance(payload, TestExecutionResult):
        result = payload
    elif isinstance(payload, dict):
        result = TestExecutionResult.model_validate(payload)
    else:
        return TestExecutionResult(
            kind="sandbox_error",
            message="Test execution returned an unexpected result.",
        )

    stdout = _bound(redact(result.stdout))
    stderr = _bound(redact(result.stderr))
    message = _bound(redact(result.message))
    parsed = parse_diagnostics(stdout, stderr)
    diagnostics = parsed or [_bound(redact(item)) for item in result.diagnostics][:20]
    kind = result.kind
    enforced = result.sandbox_enforced is True
    if kind == "passed" and not (enforced and result.exit_code == 0):
        kind = "sandbox_error"
        enforced = False
        message = "Refusing to mark tests passed without an enforced sandbox."
    signature = result.failure_signature or failure_signature(stdout, stderr)
    return result.model_copy(
        update={
            "kind": kind,
            "stdout": stdout,
            "stderr": stderr,
            "message": message,
            "diagnostics": diagnostics,
            "sandbox_enforced": enforced,
            "failure_signature": signature,
        }
    )


def parse_diagnostics(stdout: str, stderr: str) -> list[str]:
    found: list[str] = []
    for line in f"{stdout}\n{stderr}".splitlines():
        stripped = line.strip()
        if stripped.startswith(_FAILURE_PREFIXES) or "AssertionError" in stripped:
            found.append(stripped[:500])
        if len(found) == 20:
            break
    return found


def failure_signature(stdout: str, stderr: str) -> str:
    lines = parse_diagnostics(stdout, stderr)
    body = "\n".join(lines) if lines else f"{stdout}\n{stderr}"[:2000]
    compact = re.sub(r"\s+", " ", body).strip()
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def _bound(value: str) -> str:
    if len(value) <= 16_384:
        return value
    return value[:16_384] + "\n[truncated]"
