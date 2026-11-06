"""Preapproved packages baked into the sandbox image.

The model cannot add packages, and the test run cannot contact the network.
A generated requirements file is compared with this list and is never installed.
"""

from __future__ import annotations

import re
from pathlib import Path

PREAPPROVED_PACKAGES: tuple[tuple[str, str], ...] = (
    ("pytest", "8.4.1"),
    ("fastapi", "0.115.12"),
    ("uvicorn", "0.34.0"),
    ("sqlalchemy", "2.0.40"),
    ("httpx", "0.28.1"),
    ("pydantic", "2.11.7"),
)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def allowlist() -> dict[str, str]:
    return {name.lower(): version for name, version in PREAPPROVED_PACKAGES}


def requirements_text() -> str:
    return "\n".join(f"{name}=={version}" for name, version in PREAPPROVED_PACKAGES) + "\n"


def classify_requirement_lines(text: str) -> dict[str, object]:
    """Classify requirement lines without installing them."""
    approved = allowlist()
    approved_rows: list[str] = []
    unapproved: list[str] = []
    mismatched: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):
            if line.startswith(("-", "--")):
                unapproved.append(line)
            continue
        match = _NAME.match(line)
        if match is None:
            unapproved.append(line)
            continue
        name = match.group(0)
        spec = line[match.end() :].strip()
        pinned = approved.get(name.lower())
        if pinned is None:
            unapproved.append(line)
        elif spec != f"=={pinned}":
            mismatched.append(line)
        else:
            approved_rows.append(line)
    return {
        "requirements_present": True,
        "approved": approved_rows,
        "unapproved": unapproved,
        "unpinned_or_mismatched": mismatched,
        "sandbox_packages": [f"{name}=={version}" for name, version in PREAPPROVED_PACKAGES],
    }


def inspect_requirements(workspace: Path | str) -> dict[str, object]:
    """Classify a generated requirements.txt without installing anything."""
    root = Path(workspace)
    path = root / "requirements.txt"
    if not path.is_file() or path.is_symlink():
        return {
            "requirements_present": False,
            "approved": [],
            "unapproved": [],
            "unpinned_or_mismatched": [],
            "sandbox_packages": [f"{name}=={version}" for name, version in PREAPPROVED_PACKAGES],
        }
    return classify_requirement_lines(path.read_text(encoding="utf-8", errors="replace"))
