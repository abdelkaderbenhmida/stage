"""Trivy image vulnerability scanner (docs/PLATFORM_SPEC.md §5)."""

from collections.abc import Callable

from controlplane.core.config import settings
from controlplane.runners.sandbox import SandboxRun, run_sandbox
from controlplane.runners.scanners.base import SANDBOX_IMAGE, RawResult


def run_trivy(
    image_ref: str,
    on_line: Callable[[str], None] | None = None,
    timeout: int = settings.scan_timeout_seconds,
    *,
    from_registry: bool = False,
    network: str = "",
    insecure: bool = False,
) -> RawResult:
    """Scan ``image_ref`` with Trivy and return the raw JSON on stdout.

    By default Trivy resolves the image through the local Docker daemon. The
    sandbox does not mount the docker socket — deliberately, since it is
    root-equivalent on the host and is granted only for image build and push —
    so a daemon lookup fails with "failed to connect to the docker API" and
    produces no report at all.

    ``from_registry`` scans the pushed image over the registry API instead,
    which needs no socket. ``network`` joins the docker network the registry
    is on, and ``insecure`` allows a plain-HTTP local registry.
    """
    command = [
        "trivy", "image",
        "--format", "json",
        "--no-progress",
        "--ignore-unfixed",
        "--quiet",
    ]
    if from_registry:
        command += ["--image-src", "remote"]
    if insecure:
        command += ["--insecure"]
    command.append(image_ref)

    result = run_sandbox(
        SandboxRun(
            command=command,
            image=SANDBOX_IMAGE,
            network_enabled=True,
            network=network,
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
