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
        stdout=_json_document(result.output),
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_seconds=result.duration_seconds,
    )


def _json_document(output: str) -> str:
    """Pull Trivy's JSON report out of the sandbox's merged output stream.

    The sandbox runs commands with stderr folded into stdout, and Trivy logs
    to stderr, so the captured text is typically log lines followed by the
    report. `json.loads` on the whole thing fails, and because the parser
    treats unreadable output as "no findings" that failure used to read as a
    clean image — the vulnerability gate passed anything whose scan logged a
    warning. Returning just the JSON keeps the gate honest.

    Returns the original text unchanged when no JSON object is present, so a
    genuine scanner failure still looks like one to the caller.
    """
    if not output:
        return output
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1 or end < start:
        return output
    candidate = output[start : end + 1]
    import json

    try:
        json.loads(candidate)
    except ValueError:
        return output
    return candidate
