"""Real sandbox tests.

These tests start Docker. They are skipped when Docker is missing or the daemon
rejects the required security settings. They do not run model output.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

import workflows.software_team_graph as workflow
from agents.fast_mode import SIMULATED_LABEL
from execution.policy import IMAGE_NAME, SandboxPolicy
from execution.sandbox import execute_pytest, security_args
from tools.workspace import create_run_workspace

pytestmark = pytest.mark.integration


def _docker_reason() -> str:
    if shutil.which("docker") is None:
        return "Docker is not installed."
    try:
        info = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Docker is not usable: {exc}"
    if info.returncode != 0:
        detail = (info.stderr or info.stdout or "docker info failed").strip()
        return f"Docker is not available: {detail[-300:]}"
    return ""


def _project(tmp_path: Path, name: str, test_source: str) -> Path:
    workspace = create_run_workspace(name, tmp_path)
    test_path = workspace / "tests" / "test_sandbox.py"
    test_path.parent.mkdir()
    test_path.write_text(textwrap.dedent(test_source), encoding="utf-8")
    return workspace


@pytest.fixture(scope="module")
def sandbox_ready():
    reason = _docker_reason()
    if reason:
        pytest.skip(reason)
    build = subprocess.run(
        ["docker", "build", "-t", IMAGE_NAME, "sandbox"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
        check=False,
    )
    if build.returncode != 0:
        pytest.skip("Could not build the sandbox image: " + (build.stderr or "")[-400:])
    probe_dir = Path(tempfile.mkdtemp(prefix="aset-probe-"))
    try:
        probe = subprocess.run(
            security_args(SandboxPolicy(timeout_seconds=20), probe_dir, "aset-probe-policy")
            + [IMAGE_NAME, "python", "-c", "print('ok')"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
    if probe.returncode != 0:
        pytest.skip(
            "Docker rejected the required sandbox controls: " + (probe.stderr or probe.stdout)[-400:]
        )
    return True


def test_network_and_root_filesystem_are_isolated(sandbox_ready, tmp_path):
    workspace = _project(
        tmp_path,
        "dockernet1",
        """
        def test_network_is_disabled():
            import socket
            try:
                socket.create_connection(("1.1.1.1", 443), timeout=2)
            except OSError:
                return
            raise AssertionError("network access was available")

        def test_root_is_readonly():
            try:
                open("/etc/sandbox-write-probe", "w", encoding="utf-8").close()
            except OSError:
                return
            raise AssertionError("root filesystem was writable")
        """,
    )
    result = execute_pytest(workspace, "dockernet1", SandboxPolicy(timeout_seconds=20))
    assert result.sandbox_enforced is True
    assert result.kind == "passed", result.stderr + result.stdout


def test_simulated_backend_only_workflow_reaches_approval(sandbox_ready, tmp_path, monkeypatch):
    monkeypatch.setenv("FAST_DEV_MODE", "1")
    database = tmp_path / "team.db"
    state = workflow.run_software_team_workflow(
        "Build a task manager",
        workspace_root=tmp_path / "generated",
        db_path=database,
        run_id="runfastsim",
    )
    workspace = Path(state["workspace_path"])
    test_result = (state.get("test_results") or [{}])[0]

    assert state["status"] == "awaiting_approval", state.get("errors")
    assert state["execution_mode"] == "simulated"
    assert state["lifecycle_status"] == "awaiting_approval"
    assert SIMULATED_LABEL in state["requirements"]
    assert "Not real model output" in state["frontend_code"]
    assert not (workspace / "frontend" / "app.py").exists()
    qa_test = (workspace / "tests" / "test_api.py").read_text(encoding="utf-8")
    assert "streamlit" not in qa_test.lower()
    assert test_result.get("kind") == "passed", test_result
    assert test_result.get("sandbox_enforced") is True
    assert test_result.get("exit_code") == 0
    assert state["run_metrics"]["token_usage"]["reported"] is False
    with sqlite3.connect(database) as connection:
        approvals = connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
    assert approvals == 0


def test_backend_package_imports_without_init_file(sandbox_ready, tmp_path):
    workspace = create_run_workspace("runimport1", tmp_path)
    (workspace / "backend").mkdir()
    (workspace / "tests").mkdir()
    (workspace / "backend" / "main.py").write_text("VALUE = 'present'\n", encoding="utf-8")
    (workspace / "tests" / "test_import.py").write_text(
        "from backend.main import VALUE\n\ndef test_backend_is_importable():\n    assert VALUE == 'present'\n",
        encoding="utf-8",
    )
    assert not (workspace / "backend" / "__init__.py").exists()

    result = execute_pytest(workspace, "runimport1")

    assert result.sandbox_enforced is True
    assert result.kind == "passed", result.stdout + result.stderr
    assert result.exit_code == 0


def test_timeout_is_enforced(sandbox_ready, tmp_path):
    workspace = _project(
        tmp_path,
        "dockertime1",
        """
        def test_sleeps():
            import time
            time.sleep(30)
        """,
    )
    result = execute_pytest(workspace, "dockertime1", SandboxPolicy(timeout_seconds=2))
    assert result.kind == "timeout"
    assert result.sandbox_enforced is True


def test_memory_limit_is_enforced(sandbox_ready, tmp_path):
    workspace = _project(
        tmp_path,
        "dockermem01",
        """
        def test_allocation_is_limited():
            blob = bytearray(128 * 1024 * 1024)
            assert len(blob) < 128 * 1024 * 1024
        """,
    )
    result = execute_pytest(
        workspace,
        "dockermem01",
        SandboxPolicy(memory="32m", memory_swap="32m", timeout_seconds=20),
    )
    assert result.sandbox_enforced is True
    assert result.kind in {"failed", "execution_error"}
    if result.kind == "failed":
        assert "MemoryError" in (result.stdout + result.stderr)
