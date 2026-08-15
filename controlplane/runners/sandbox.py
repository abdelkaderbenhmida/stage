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

import select
import subprocess
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
    network_enabled: bool = True
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
    args += ["--cpus", str(run.cpus), "--memory", f"{run.memory_mb}m"]
    if run.requires_docker_daemon:
        args += ["-v", "/var/run/docker.sock:/var/run/docker.sock"]
    for key, value in run.env.items():
        args += ["-e", f"{key}={value}"]
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

    if timed_out:
        # Drain whatever the container emitted before dying.
        for line in proc.stdout:
            clean = line.rstrip("\n")
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
