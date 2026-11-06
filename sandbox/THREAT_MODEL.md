# Sandbox threat model

The execution service runs generated pytest files in a Docker container. Docker is a useful isolation control. It is not a complete security boundary. A kernel bug, a container runtime bug, or a misconfigured daemon can expose the host. This project refuses to run generated code when the controls below cannot be applied, and it does not claim that a successful run is safe to deploy.

## Assets

- The host filesystem, credentials, SSH keys, and environment secrets
- The Docker daemon and any other containers it can control
- Generated project files that have not been approved
- Workflow state in SQLite, including approval records

## Untrusted inputs

- Model responses and the files parsed from them
- Generated tests and the code they import
- pytest stdout, stderr, and exit codes
- Repair patches proposed by the model

## Controls

- Generated pytest runs only through `execution.sandbox.execute_pytest`.
- The process command is an argument list. The service does not use a shell.
- The container command is the fixed pytest command. The model cannot choose it.
- The container user is `65534:65534`.
- The root filesystem is read-only. Writable space is a size-capped tmpfs on `/tmp`.
- The network mode is `none`.
- Capabilities are dropped, privileged mode is absent, and `no-new-privileges` is set.
- CPU, memory, memory-swap, PID, output, and wall-clock limits are set.
- The daemon socket, host credentials, host environment secrets, and SSH keys are not mounted.
- No host port is published.
- The mounted project is a copy of the workspace, mounted read-only. Repair writes happen on the host only after path and syntax checks.
- Packages are not installed during a run. The image contains pytest and the Python standard library.
- Results are truncated and passed through the same secret redaction used for SQLite.
- An approval record is stored for one run id and one artifact hash. A later file change makes that approval stale.

## What this does not stop

- Escape from a vulnerable container runtime or Linux kernel
- A malicious Docker daemon, image, or host user who can change this code
- Resource exhaustion on the Docker daemon itself
- Exfiltration through a future change that adds network or extra mounts
- Tests that import libraries the image does not contain; those fail rather than being installed
- A generated requirements file that asks for a package outside the pinned allowlist. That request is recorded and not installed
- A person deleting SQLite rows or checkpoint data and starting a new run
- Symlink swaps on the host during the copy into the snapshot
- Secret patterns that do not match the redaction expression

Generated output that passes pytest is still untrusted application code. Approval records a human decision. It does not make the code correct or safe to publish.
