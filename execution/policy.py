"""Docker sandbox policy.

These controls are necessary, and they are not a complete security boundary.
See sandbox/THREAT_MODEL.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

IMAGE_NAME = "autonomous-team-sandbox:local"
ALLOWED_PYTEST_COMMAND = (
    "pytest",
    "-q",
    "--tb=line",
    "--disable-warnings",
    "-p",
    "no:cacheprovider",
)
_MEMORY = re.compile(r"([1-9][0-9]*)(m|g)")
_SIZE = re.compile(r"([1-9][0-9]*)(m|g)")


class SandboxPolicyError(RuntimeError):
    """Raised when a sandbox invocation cannot enforce the required controls."""


@dataclass(frozen=True)
class SandboxPolicy:
    image: str = IMAGE_NAME
    timeout_seconds: int = 20
    memory: str = "256m"
    memory_swap: str = "256m"
    cpus: str = "0.5"
    pids_limit: str = "64"
    tmpfs_size: str = "32m"
    output_limit_bytes: int = 16_384
    user: str = "65534:65534"
    network: str = "none"
    cap_drop: str = "ALL"

    def __post_init__(self) -> None:
        if self.image != IMAGE_NAME:
            raise SandboxPolicyError("The sandbox image name is fixed.")
        if self.network != "none":
            raise SandboxPolicyError("Sandbox network must be none.")
        if self.user != "65534:65534":
            raise SandboxPolicyError("Sandbox user must be 65534:65534.")
        if self.cap_drop != "ALL":
            raise SandboxPolicyError("Sandbox must drop all Linux capabilities.")
        if not 1 <= self.timeout_seconds <= 60:
            raise SandboxPolicyError("Sandbox timeout must be from 1 to 60 seconds.")
        if not 1024 <= self.output_limit_bytes <= 64_000:
            raise SandboxPolicyError("Sandbox output limit is out of range.")
        memory = _within(self.memory, 256, "memory")
        swap = _within(self.memory_swap, 256, "memory swap")
        if swap > memory:
            raise SandboxPolicyError("memory-swap cannot exceed the memory limit.")
        _within(self.tmpfs_size, 64, "tmpfs size")
        if not re.fullmatch(r"[1-9][0-9]*", self.pids_limit):
            raise SandboxPolicyError("pids-limit must be a positive integer.")
        if int(self.pids_limit) > 128:
            raise SandboxPolicyError("pids-limit cannot exceed 128.")
        if not re.fullmatch(r"0\.[1-9]|1\.0", self.cpus):
            raise SandboxPolicyError("cpu limit must be from 0.1 through 1.0.")


def default_policy() -> SandboxPolicy:
    return SandboxPolicy()


def _within(value: str, maximum_mb: int, label: str) -> int:
    match = _MEMORY.fullmatch(value) if label.startswith("memory") else _SIZE.fullmatch(value)
    if match is None:
        raise SandboxPolicyError(f"{label} must look like 32m or 1g.")
    amount = int(match.group(1))
    unit = match.group(2)
    megabytes = amount * 1024 if unit == "g" else amount
    if megabytes < 16 or megabytes > maximum_mb:
        raise SandboxPolicyError(f"{label} must be from 16m through {maximum_mb}m.")
    return megabytes
