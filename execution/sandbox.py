"""Build and run the fixed pytest command inside the sandbox.

The workflow calls execute_pytest. It does not accept a caller-supplied shell
command. If a required control is missing, this module returns a sandbox error
and does not run the generated tests on the host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from execution.policy import (
    ALLOWED_PYTEST_COMMAND,
    SandboxPolicy,
    SandboxPolicyError,
    default_policy,
)
from execution.results import TestExecutionResult, failure_signature, parse_diagnostics
from tools.workspace import WorkspaceSecurityError, list_relative_files, read_text

_FORBIDDEN_MOUNT_PARTS = {
    ".ssh",
    ".aws",
    ".docker",
    ".gnupg",
    "credentials",
    "secrets",
}
_FORBIDDEN_ARGV = {
    "--privileged",
    "--cap-add",
    "--network=host",
    "--pid=host",
    "--ipc=host",
    "--publish",
    "--publish-all",
    "-P",
    "--env-file",
    "--volumes-from",
}


def security_args(policy: SandboxPolicy, snapshot: Path, name: str) -> list[str]:
    """Return the docker arguments that carry the sandbox controls."""
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        policy.network,
        "--cap-drop",
        policy.cap_drop,
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        policy.pids_limit,
        "--memory",
        policy.memory,
        "--memory-swap",
        policy.memory_swap,
        "--cpus",
        policy.cpus,
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={policy.tmpfs_size}",
        "--user",
        policy.user,
        "--workdir",
        "/workspace",
        "--mount",
        f"type=bind,source={snapshot},target=/workspace,readonly",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "HOME=/tmp",
        "-e",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        "-e",
        "PYTHONPATH=/workspace",
    ]


def build_pytest_argv(policy: SandboxPolicy, snapshot: Path, name: str) -> list[str]:
    return [
        *security_args(policy, snapshot, name),
        policy.image,
        *ALLOWED_PYTEST_COMMAND,
    ]


def assert_security_args(argv: list[str], policy: SandboxPolicy) -> None:
    """Reject an invocation that drops or weakens a required control."""
    if not argv or argv[0] != "docker" or "run" not in argv[:3]:
        raise SandboxPolicyError("Sandbox command must be docker run.")
    if policy.image not in argv:
        raise SandboxPolicyError("Sandbox command does not use the fixed image.")
    docker_args = argv[: argv.index(policy.image)]
    if "--rm" not in docker_args:
        raise SandboxPolicyError("Sandbox command must remove the container.")
    _require_pair(docker_args, "--network", policy.network)
    _require_pair(docker_args, "--cap-drop", policy.cap_drop)
    _require_pair(docker_args, "--security-opt", "no-new-privileges:true")
    _require_pair(docker_args, "--pids-limit", policy.pids_limit)
    _require_pair(docker_args, "--memory", policy.memory)
    _require_pair(docker_args, "--memory-swap", policy.memory_swap)
    _require_pair(docker_args, "--cpus", policy.cpus)
    _require_pair(docker_args, "--user", policy.user)
    if "--read-only" not in docker_args:
        raise SandboxPolicyError("Sandbox root filesystem must be read-only.")
    if not any(item.startswith("/tmp:rw,noexec,nosuid,nodev,size=") for item in docker_args):
        raise SandboxPolicyError("Sandbox temporary storage is missing or writable without limits.")
    if any(item in _FORBIDDEN_ARGV or item == "-p" for item in docker_args):
        raise SandboxPolicyError("Sandbox command contains a forbidden option.")
    if "docker.sock" in " ".join(docker_args):
        raise SandboxPolicyError("Sandbox command mounts the Docker socket.")
    _assert_workspace_pythonpath(docker_args)
    _assert_mounts_are_workspace_only(docker_args)


def assert_pytest_invocation(argv: list[str], policy: SandboxPolicy) -> None:
    assert_security_args(argv, policy)
    image_at = argv.index(policy.image)
    command = tuple(argv[image_at + 1 :])
    if command != ALLOWED_PYTEST_COMMAND:
        raise SandboxPolicyError("Sandbox command is not the allowlisted pytest command.")


def execute_pytest(
    workspace: Path,
    run_id: str,
    policy: SandboxPolicy | None = None,
) -> TestExecutionResult:
    """Copy a workspace and run the allowlisted pytest command in a new container."""
    selected = policy or default_policy()
    command = list(ALLOWED_PYTEST_COMMAND)
    if shutil.which("docker") is None:
        return _sandbox_error(
            command,
            "Docker is not available. Refusing to run generated code on the host.",
        )
    try:
        source = _real_workspace(workspace)
        snapshot = _snapshot_workspace(source)
    except (SandboxPolicyError, WorkspaceSecurityError, OSError) as exc:
        return _sandbox_error(command, str(exc))

    name = f"aset-sbx-{uuid.uuid4().hex[:12]}"
    argv = build_pytest_argv(selected, snapshot, name)
    started = time.monotonic()
    try:
        assert_pytest_invocation(argv, selected)
        exit_code, stdout, stderr, timed_out, truncated = _run_captured(
            argv,
            timeout_seconds=selected.timeout_seconds,
            output_limit=selected.output_limit_bytes,
            container_name=name,
        )
    except SandboxPolicyError as exc:
        return _sandbox_error(command, str(exc))
    except OSError as exc:
        return _sandbox_error(command, f"Unable to start the sandbox: {exc}")
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)

    duration_ms = int((time.monotonic() - started) * 1000)
    if truncated:
        stderr = f"{stderr}\n[output truncated]".strip()
    return _classify(command, exit_code, stdout, stderr, duration_ms, timed_out)


def _classify(
    command: list[str],
    exit_code: int | None,
    stdout: str,
    stderr: str,
    duration_ms: int,
    timed_out: bool,
) -> TestExecutionResult:
    diagnostics = parse_diagnostics(stdout, stderr)
    signature = failure_signature(stdout, stderr)
    common = {
        "command": command,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "sandbox_enforced": True,
        "failure_signature": signature,
        "diagnostics": diagnostics,
    }
    if timed_out:
        return TestExecutionResult(
            kind="timeout",
            message="Sandbox execution exceeded the time limit and was stopped.",
            **common,
        )
    if exit_code in {125, 126, 127}:
        return TestExecutionResult(
            kind="sandbox_error",
            message=stderr or "The sandbox container could not be started.",
            **{**common, "sandbox_enforced": False},
        )
    if exit_code == 137:
        return TestExecutionResult(
            kind="execution_error",
            message="The sandbox stopped the process, which can mean the memory limit was reached.",
            **common,
        )
    if exit_code == 0:
        return TestExecutionResult(kind="passed", message="Sandbox tests passed.", **common)
    if exit_code == 1:
        return TestExecutionResult(kind="failed", message="Sandbox tests failed.", **common)
    if exit_code == 5:
        return TestExecutionResult(
            kind="execution_error",
            message="pytest did not collect any tests.",
            **common,
        )
    return TestExecutionResult(
        kind="execution_error",
        message=f"Sandbox pytest exited with code {exit_code}.",
        **common,
    )


def _sandbox_error(command: list[str], message: str) -> TestExecutionResult:
    return TestExecutionResult(
        kind="sandbox_error",
        command=command,
        message=message,
        sandbox_enforced=False,
    )


def _real_workspace(workspace: Path) -> Path:
    if workspace.is_symlink():
        raise SandboxPolicyError("Refusing to execute a symlinked workspace.")
    if not workspace.is_dir():
        raise SandboxPolicyError("Workspace is not a directory.")
    return workspace


def _snapshot_workspace(workspace: Path) -> Path:
    snapshot = Path(tempfile.mkdtemp(prefix="aset-snapshot-"))
    try:
        for relative in list_relative_files(workspace):
            content = read_text(workspace, relative)
            target = snapshot / relative
            if target.is_symlink():
                raise SandboxPolicyError(f"Refusing to copy a symbolic link: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return snapshot
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise


def _run_captured(
    argv: list[str],
    *,
    timeout_seconds: int,
    output_limit: int,
    container_name: str,
) -> tuple[int | None, str, str, bool, bool]:
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    truncated = threading.Event()
    threads = [
        threading.Thread(
            target=_pump,
            args=(process.stdout, output_limit, stdout_parts, truncated),
            daemon=True,
        ),
        threading.Thread(
            target=_pump,
            args=(process.stderr, output_limit, stderr_parts, truncated),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None and time.monotonic() < deadline:
        if truncated.is_set():
            _docker_kill(container_name)
            process.kill()
            break
        time.sleep(0.05)
    if process.poll() is None:
        timed_out = not truncated.is_set()
        _docker_kill(container_name)
        process.kill()
    for thread in threads:
        thread.join(timeout=2)
    return process.returncode, "".join(stdout_parts), "".join(stderr_parts), timed_out, truncated.is_set()


def _pump(pipe, limit: int, sink: list[str], truncated: threading.Event) -> None:
    if pipe is None:
        return
    size = 0
    try:
        while True:
            block = pipe.read(1024)
            if not block:
                break
            room = limit - size
            if room <= 0:
                truncated.set()
                break
            piece = block[:room]
            sink.append(piece)
            size += len(piece)
            if len(block) > room:
                truncated.set()
                break
    finally:
        pipe.close()


def _docker_kill(name: str) -> None:
    try:
        subprocess.run(
            ["docker", "kill", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            shell=False,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def _require_pair(argv: list[str], flag: str, expected: str) -> None:
    indexes = [index for index, item in enumerate(argv) if item == flag]
    if not indexes:
        raise SandboxPolicyError(f"Sandbox command is missing {flag}.")
    for index in indexes:
        if index + 1 >= len(argv) or argv[index + 1] != expected:
            raise SandboxPolicyError(f"Sandbox command does not enforce {flag} {expected}.")


def _assert_workspace_pythonpath(argv: list[str]) -> None:
    """Allow only the mounted project root, not a host path or an extra entry."""
    found = [item for item in argv if "PYTHONPATH" in item]
    if found != ["PYTHONPATH=/workspace"]:
        raise SandboxPolicyError("Sandbox PYTHONPATH must be exactly /workspace.")


def _assert_mounts_are_workspace_only(argv: list[str]) -> None:
    for index, item in enumerate(argv):
        if item in {"-v", "--volume"}:
            raise SandboxPolicyError("Sandbox command uses a volume flag.")
        if item != "--mount" or index + 1 >= len(argv):
            continue
        spec = argv[index + 1]
        if "docker.sock" in spec or "readonly" not in spec or "target=/workspace" not in spec:
            raise SandboxPolicyError("Sandbox mount is not limited to a read-only workspace.")
        source = ""
        for part in spec.split(","):
            if part.startswith("source="):
                source = part.split("=", 1)[1]
        if not source:
            raise SandboxPolicyError("Sandbox mount source is missing.")
        parts = {piece.lower() for piece in Path(source).parts}
        if parts & _FORBIDDEN_MOUNT_PARTS:
            raise SandboxPolicyError("Sandbox mount includes a credential path.")
