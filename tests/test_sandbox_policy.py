import subprocess
from pathlib import Path

import pytest

from execution.policy import ALLOWED_PYTEST_COMMAND, IMAGE_NAME, SandboxPolicy, SandboxPolicyError
from execution.results import normalize_test_result
from execution.sandbox import (
    assert_pytest_invocation,
    build_pytest_argv,
    execute_pytest,
)
from tools.code_runner import run_python_file
from tools.workspace import create_run_workspace, write_text_atomic


def _workspace(tmp_path):
    workspace = create_run_workspace("runsandbox1", tmp_path)
    write_text_atomic(workspace, "tests/test_api.py", "def test_ok():\n    assert True\n")
    return workspace


def test_pytest_command_contains_required_controls(tmp_path):
    policy = SandboxPolicy()
    argv = build_pytest_argv(policy, tmp_path, "aset-sbx-test")
    assert_pytest_invocation(argv, policy)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--cap-drop" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges:true" in argv
    assert "--read-only" in argv
    assert argv[argv.index("--user") + 1] == "65534:65534"
    assert argv[argv.index("--memory") + 1] == "256m"
    assert argv[argv.index("--pids-limit") + 1] == "64"
    assert "docker.sock" not in " ".join(argv)
    assert "--privileged" not in argv
    assert argv[argv.index(IMAGE_NAME) + 1 :] == list(ALLOWED_PYTEST_COMMAND)
    assert argv.count("PYTHONPATH=/workspace") == 1


def test_pythonpath_must_be_the_workspace_mount(tmp_path):
    policy = SandboxPolicy()
    argv = build_pytest_argv(policy, tmp_path, "aset-sbx-test")
    missing = [item for item in argv if item != "PYTHONPATH=/workspace"]
    widened = [
        "PYTHONPATH=/workspace:/tmp" if item == "PYTHONPATH=/workspace" else item
        for item in argv
    ]
    host_path = [
        "PYTHONPATH=/Users/project" if item == "PYTHONPATH=/workspace" else item
        for item in argv
    ]
    for changed in (missing, widened, host_path):
        with pytest.raises(SandboxPolicyError, match="PYTHONPATH"):
            assert_pytest_invocation(changed, policy)


def test_weakened_sandbox_command_is_rejected(tmp_path):
    policy = SandboxPolicy()
    argv = build_pytest_argv(policy, tmp_path, "aset-sbx-test")
    weakened = [item for item in argv if item not in {"--read-only", "--network", "none"}]
    with pytest.raises(SandboxPolicyError):
        assert_pytest_invocation(weakened, policy)


def test_policy_rejects_network_and_privileged_changes():
    with pytest.raises(SandboxPolicyError):
        SandboxPolicy(network="bridge")
    with pytest.raises(SandboxPolicyError):
        SandboxPolicy(user="0:0")


def test_execute_refuses_host_when_docker_is_missing(monkeypatch, tmp_path):
    workspace = _workspace(tmp_path)

    def fail_run(*_args, **_kwargs):
        raise AssertionError("generated code reached the host subprocess")

    monkeypatch.setattr("execution.sandbox.shutil.which", lambda _name: None)
    monkeypatch.setattr("execution.sandbox.subprocess.Popen", fail_run)
    monkeypatch.setattr("execution.sandbox.subprocess.run", fail_run)

    result = execute_pytest(workspace, "runsandbox1")

    assert result.kind == "sandbox_error"
    assert result.sandbox_enforced is False
    assert "host" in result.message


def test_execute_rejects_a_command_that_drops_controls(monkeypatch, tmp_path):
    workspace = _workspace(tmp_path)
    monkeypatch.setattr("execution.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        "execution.sandbox.build_pytest_argv",
        lambda *_args, **_kwargs: ["docker", "run", "--rm", IMAGE_NAME, "python"],
    )

    def fail_run(*_args, **_kwargs):
        raise AssertionError("unsafe sandbox command was started")

    monkeypatch.setattr("execution.sandbox.subprocess.Popen", fail_run)
    result = execute_pytest(workspace, "runsandbox1")

    assert result.kind == "sandbox_error"
    assert result.sandbox_enforced is False


def test_timeout_stops_the_container(monkeypatch, tmp_path):
    workspace = _workspace(tmp_path)
    calls = []

    class _Process:
        def __init__(self):
            self.stdout = _Empty()
            self.stderr = _Empty()
            self.returncode = None

        def poll(self):
            return None

        def kill(self):
            self.returncode = -9

    class _Empty:
        def read(self, _size):
            return ""

        def close(self):
            return None

    monkeypatch.setattr("execution.sandbox.shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr("execution.sandbox.subprocess.Popen", lambda *_args, **_kwargs: _Process())

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("execution.sandbox.subprocess.run", fake_run)
    result = execute_pytest(workspace, "runsandbox1", SandboxPolicy(timeout_seconds=1))

    assert result.kind == "timeout"
    assert result.sandbox_enforced is True
    assert calls and calls[0][:2] == ["docker", "kill"]


def test_symlinked_workspace_is_not_executed(monkeypatch, tmp_path):
    workspace = _workspace(tmp_path)
    link = tmp_path / "linked"
    link.symlink_to(workspace, target_is_directory=True)
    monkeypatch.setattr("execution.sandbox.shutil.which", lambda _name: "/usr/bin/docker")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("symlinked workspace was executed")

    monkeypatch.setattr("execution.sandbox.subprocess.Popen", fail_run)
    result = execute_pytest(link, "runsandbox1")

    assert result.kind == "sandbox_error"
    assert "symlink" in result.message.lower()


def test_normalize_refuses_unverified_pass_and_redacts_secrets():
    result = normalize_test_result(
        {
            "kind": "passed",
            "exit_code": 0,
            "sandbox_enforced": False,
            "stdout": "token = supersecret",
            "stderr": "",
            "command": list(ALLOWED_PYTEST_COMMAND),
        }
    )

    assert result.kind == "sandbox_error"
    assert result.sandbox_enforced is False
    assert "supersecret" not in result.stdout
    assert "[REDACTED]" in result.stdout


def test_run_python_file_never_starts_a_process(monkeypatch, tmp_path):
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("host process started")

    monkeypatch.setattr(subprocess, "run", fail_run)
    monkeypatch.setattr(subprocess, "Popen", fail_run)
    result = run_python_file("demo", str(tmp_path / "main.py"))

    assert result["status"] == "error"
    assert result["sandbox_enforced"] is False
    assert "host" in result["stderr"]


def test_failure_diagnostics_are_parsed():
    result = normalize_test_result(
        {
            "kind": "failed",
            "exit_code": 1,
            "sandbox_enforced": True,
            "stdout": "FAILED tests/test_api.py::test_ok - AssertionError",
            "stderr": "E AssertionError",
            "command": list(ALLOWED_PYTEST_COMMAND),
        }
    )

    assert result.kind == "failed"
    assert any(item.startswith("FAILED ") for item in result.diagnostics)
    assert result.failure_signature
