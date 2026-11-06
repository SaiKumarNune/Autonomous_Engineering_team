import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from tools.file_writer import list_project_files, read_file, write_file
from tools.project_scanner import get_project_tree, list_files, read_project_file
from tools.workspace import (
    WorkspaceSecurityError,
    build_project_zip,
    create_run_workspace,
    write_text_atomic,
)


def test_workspace_is_isolated_per_run(tmp_path):
    first = create_run_workspace("runone01", tmp_path)
    second = create_run_workspace("runtwo02", tmp_path)
    write_text_atomic(first, "backend/main.py", "print('one')\n")

    assert first != second
    assert not (second / "backend" / "main.py").exists()
    with pytest.raises(WorkspaceSecurityError, match="already exists"):
        create_run_workspace("runone01", tmp_path)


@pytest.mark.parametrize(
    "relative_path",
    ["../outside.py", "/tmp/outside.py", "nested/../../outside.py", "bad\\path.py"],
)
def test_path_traversal_is_rejected(tmp_path, relative_path):
    workspace = create_run_workspace("runsafe01", tmp_path)
    outside = tmp_path / "outside.py"

    with pytest.raises(WorkspaceSecurityError):
        write_text_atomic(workspace, relative_path, "print('no')\n")

    assert not outside.exists()


def test_symlink_escape_is_rejected(tmp_path):
    workspace = create_run_workspace("runlink01", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceSecurityError, match="symbolic link"):
        write_text_atomic(workspace, "escape/pwned.py", "print('no')\n")
    with pytest.raises(WorkspaceSecurityError, match="symbolic link"):
        read_file("runlink01", "escape/secret.txt", root=tmp_path)

    assert not (outside / "pwned.py").exists()


def test_duplicate_write_is_rejected(tmp_path):
    workspace = create_run_workspace("rundupe01", tmp_path)
    write_text_atomic(workspace, "backend/main.py", "print('ok')\n")

    with pytest.raises(WorkspaceSecurityError, match="duplicate"):
        write_text_atomic(workspace, "backend/main.py", "print('again')\n")


def test_file_size_limit_is_enforced(tmp_path):
    workspace = create_run_workspace("runsize01", tmp_path)

    with pytest.raises(WorkspaceSecurityError, match="exceeds"):
        write_text_atomic(workspace, "backend/main.py", "x" * 20, max_bytes=10)

    assert not (workspace / "backend" / "main.py").exists()


def test_failed_write_when_parent_is_a_file(tmp_path):
    workspace = create_run_workspace("runfail01", tmp_path)
    (workspace / "blocked").write_text("not a directory", encoding="utf-8")

    with pytest.raises(WorkspaceSecurityError, match="failed to write|is a file"):
        write_text_atomic(workspace, "blocked/child.py", "print('ok')\n")

    assert not (workspace / "blocked" / "child.py").exists()


def test_file_writer_and_scanner_share_safe_workspace(tmp_path):
    write_file("runscan01", "backend/main.py", "print('scan')\n", root=tmp_path)

    assert list_project_files("runscan01", root=tmp_path) == ["backend/main.py"]
    assert list_files("runscan01", base_dir=tmp_path) == ["backend/main.py"]
    assert read_project_file("runscan01", "backend/main.py", base_dir=tmp_path) == "print('scan')\n"
    assert "backend" in get_project_tree("runscan01", base_dir=tmp_path)


def test_zip_contains_only_regular_validated_files(tmp_path):
    workspace = create_run_workspace("runzip001", tmp_path)
    write_text_atomic(workspace, "backend/main.py", "print('ok')\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    (workspace / "notes.exe").write_bytes(b"binary")

    payload = build_project_zip(workspace)
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = archive.namelist()
        assert names == ["backend/main.py"]
        assert archive.read("backend/main.py") == b"print('ok')\n"
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)


def test_disallowed_extension_is_rejected(tmp_path):
    workspace = create_run_workspace("runext001", tmp_path)

    with pytest.raises(WorkspaceSecurityError, match="not allowed"):
        write_file("runext001", "backend/app.exe", "nope", root=tmp_path)

    assert list(workspace.rglob("*.exe")) == []
