"""Artifact hashes for approval binding."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tools.workspace import list_relative_files, read_text


def artifact_hash(workspace: Path | str) -> str:
    """Hash the regular files currently stored for one run."""
    root = Path(workspace)
    digest = hashlib.sha256()
    for relative in list_relative_files(root):
        content = read_text(root, relative)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
