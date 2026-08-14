"""pip-audit dependency scanner (docs/PLATFORM_SPEC.md §5).

Scans a requirements.txt inside a cloned repo. Queries the PyPI OSV index,
so the sandbox runs with network enabled.
"""

from collections.abc import Callable
from pathlib import Path

from controlplane.core.config import settings
from controlplane.runners.sandbox import SandboxRun, run_sandbox
from controlplane.runners.scanners.base import SANDBOX_IMAGE, RawResult


def run_pip_audit(
    requirements_file: Path,
    on_line: Callable[[str], None] | None = None,
    timeout: int = settings.scan_timeout_seconds,
) -> RawResult:
    result = run_sandbox(
        SandboxRun(
            command=[
                "pip-audit",
                "--requirement", str(requirements_file),
                "--format", "json",
                "--no-deps",
            ],
            image=SANDBOX_IMAGE,
            workspace=requirements_file.parent,
            writable_paths=[],
            network_enabled=True,
            timeout_seconds=timeout,
            on_line=on_line,
        )
    )
    return RawResult(
        tool="pip_audit",
        target=str(requirements_file),
        stdout=result.output,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_seconds=result.duration_seconds,
    )
