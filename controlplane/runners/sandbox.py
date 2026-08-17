"""Container-isolated command execution (docs/PLATFORM_SPEC.md §7.2).

Every external tool (terraform, ansible-playbook, trivy, gitleaks,
pip-audit, git, docker build) runs inside an ephemeral container that is
destroyed afterwards. Key properties:

- Fresh container per run, removed on success and failure.
- Rendered workspace is mounted read-only except explicitly writable paths.
- ``--network=none`` when the tool needs no network.
- No host credentials mounted; secrets are injected as env vars scoped to the
  run. The docker socket is mounted ONLY when ``requires_docker_daemon`` is
  explicitly set (image build / registry push).
- CPU and memory limits.
- A wall-clock timeout after which the container is killed.
- Output is scrubbed per §7.4 before being emitted.
"""

import os
import select
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from controlplane.core.config import settings
from controlplane.core.redaction import scrub_line

DEFAULT_IMAGE = settings.sandbox_image


class SandboxError(RuntimeError):
    pass


@dataclass
class SandboxResult:
    exit_code: int
    output: str
    duration_seconds: float
    timed_out: bool = False


@dataclass
class SandboxRun:
    command: list[str]
    image: str = DEFAULT_IMAGE
    workspace: Path | None = None
    workspace_writable: bool = False
    writable_paths: list[str] = field(default_factory=list)
    mounts: list[tuple[Path, str, bool]] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # Secrets. Kept apart from `env` because these must never appear in the
    # `docker run` argv — argv is world-readable through /proc, so `-e
    # TOKEN=...` hands the value to every local user for the lifetime of the
    # run. These are written to a private file and passed with --env-file.
    secret_env: dict[str, str] = field(default_factory=dict)
    network_enabled: bool = True
    # Join a named docker network. Needed to reach a service that is not
    # published on the host — the image registry is bound to 127.0.0.1 and is
    # otherwise invisible from inside a sandbox container. This grants no
    # privilege the sandbox did not already have with network_enabled.
    network: str = ""
    cpus: float = settings.sandbox_cpus
    memory_mb: int = settings.sandbox_memory_mb
    timeout_seconds: int = 300
    requires_docker_daemon: bool = False
    name: str = ""
    on_line: Callable[[str], None] | None = None


def _mount_flag(host_path: Path, container_path: str, readonly: bool) -> list[str]:
    return ["--mount", f"type=bind,source={host_path},target={container_path},readonly={readonly}"]


def _container_image_ref(image: str) -> str:
    """Sandbox runs may pass a scratch build context via the image field."""
    return image


def run_sandbox(run: SandboxRun) -> SandboxResult:
    """Execute ``run`` and stream scrubbed output until it finishes or times out."""
    name = run.name or f"ctl-{uuid.uuid4().hex[:12]}"

    args = ["docker", "run", "--name", name, "--rm"]
    if run.workspace is not None:
        workspace = run.workspace.resolve()
        args += _mount_flag(workspace, str(workspace), readonly=not run.workspace_writable)
        args += ["-w", str(workspace)]
        # File-level writable overlays only help tools that write in place.
        # Tools that replace a file via write-temp-then-rename (terraform's
        # lock file, state file) need the *containing directory* writable,
        # which workspace_writable=True grants — these per-file mounts are
        # then redundant but harmless.
        if not run.workspace_writable:
            for relative in run.writable_paths:
                writable = (workspace / relative).resolve()
                writable.parent.mkdir(parents=True, exist_ok=True)
                args += _mount_flag(writable, str(writable), readonly=False)
    for host_path, container_path, readonly in run.mounts:
        args += _mount_flag(host_path.resolve(), container_path, readonly)
    if not run.network_enabled:
        args += ["--network", "none"]
    elif run.network:
        args += ["--network", run.network]
    args += ["--cpus", str(run.cpus), "--memory", f"{run.memory_mb}m"]
    if run.requires_docker_daemon:
        args += ["-v", "/var/run/docker.sock:/var/run/docker.sock"]
    for key, value in run.env.items():
        args += ["-e", f"{key}={value}"]

    # Secrets go through a 0600 file that only this process can read, and it
    # is removed as soon as the container has consumed it.
    secret_file: Path | None = None
    if run.secret_env:
        handle, path = tempfile.mkstemp(prefix="ctl-secret-", text=True)
        secret_file = Path(path)
        with os.fdopen(handle, "w") as fh:
            for key, value in run.secret_env.items():
                # --env-file parses KEY=VALUE per line and does no quoting, so
                # a newline in a value would forge an extra variable.
                fh.write(f"{key}={value.replace(chr(10), '')}\n")
        secret_file.chmod(0o600)
        args += ["--env-file", str(secret_file)]

    args += [_container_image_ref(run.image), *run.command]

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    start = time.monotonic()
    deadline = start + run.timeout_seconds
    lines: list[str] = []
    timed_out = False

    try:
        while proc.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 0.25))
            if not ready:
                continue
            line = proc.stdout.readline()
            if line == "":
                continue
            clean = scrub_line(line.rstrip("\n")) if run.on_line else line.rstrip("\n")
            lines.append(clean)
            if run.on_line:
                run.on_line(clean)
    finally:
        if timed_out:
            subprocess.run(["docker", "kill", "-f", name], capture_output=True)
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        # Guaranteed cleanup on both success and failure paths.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        if secret_file is not None:
            secret_file.unlink(missing_ok=True)

    # Drain the pipe on every path, not just the timeout one.
    #
    # The read loop above runs `while proc.poll() is None`, so it stops the
    # instant the container exits — and anything still sitting in the pipe
    # buffer at that moment was silently dropped. A command that writes more
    # than the buffer holds and then exits promptly, which is every image
    # scan (Trivy's report here was 295 KB), had its output truncated
    # mid-document while the exit code still said success.
    #
    # That is what made the vulnerability gate unreliable in practice: a
    # truncated report is invalid JSON, the parser treated unreadable output
    # as "no findings", and the deployment proceeded. The scan looked like it
    # had run, because it had — only its conclusion never arrived.
    for line in proc.stdout:
        clean = scrub_line(line.rstrip("\n")) if run.on_line else line.rstrip("\n")
        lines.append(clean)
        if run.on_line:
            run.on_line(clean)

    output = "\n".join(lines)
    if timed_out:
        output += f"\n[command exceeded {run.timeout_seconds}s wall-clock limit; killed]"

    return SandboxResult(
        exit_code=-1 if timed_out else proc.returncode,
        output=output,
        duration_seconds=round(time.monotonic() - start, 2),
        timed_out=timed_out,
    )
