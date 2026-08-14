"""Trivy image vulnerability scanner (docs/PLATFORM_SPEC.md §5)."""

from collections.abc import Callable

from controlplane.core.config import settings
from controlplane.runners.sandbox import SandboxRun, run_sandbox
from controlplane.runners.scanners.base import SANDBOX_IMAGE, RawResult


def run_trivy(
    image_ref: str,
    on_line: Callable[[str], None] | None = None,
    timeout: int = settings.scan_timeout_seconds,
) -> RawResult:
    """Scan ``image_ref`` with Trivy and return the raw JSON on stdout."""
    result = run_sandbox(
        SandboxRun(
            command=[
                "trivy", "image",
                "--format", "json",
                "--no-progress",
                "--ignore-unfixed",
                "--quiet",
                image_ref,
            ],
            image=SANDBOX_IMAGE,
            network_enabled=True,
            timeout_seconds=timeout,
            on_line=on_line,
        )
    )
    return RawResult(
        tool="trivy",
        target=image_ref,
        stdout=result.output,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_seconds=result.duration_seconds,
    )
