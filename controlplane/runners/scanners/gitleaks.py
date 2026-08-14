"""Gitleaks secret scanner (docs/PLATFORM_SPEC.md §5).

Runs against an already-cloned repo with ``--network=none``. The JSON report
is written to an ephemeral writable mount and read back by the caller.
"""

import tempfile
from collections.abc import Callable
from pathlib import Path

from controlplane.core.config import settings
from controlplane.runners.sandbox import SandboxRun, run_sandbox
from controlplane.runners.scanners.base import SANDBOX_IMAGE, RawResult


def run_gitleaks(
    repo_path: Path,
    on_line: Callable[[str], None] | None = None,
    timeout: int = settings.scan_timeout_seconds,
) -> RawResult:
    tmpdir = tempfile.mkdtemp(prefix="ctl-gitleaks-")
    report_host = Path(tmpdir) / "report.json"
    try:
        result = run_sandbox(
            SandboxRun(
                command=[
                    "gitleaks", "detect",
                    "--source", str(repo_path),
                    "--report-format", "json",
                    "--report-path", "/tmp/report.json",
                    "--no-banner",
                    "--redact", "true",
                ],
                image=SANDBOX_IMAGE,
                workspace=repo_path,
                writable_paths=[],
                mounts=[(report_host, "/tmp/report.json", False)],
                network_enabled=False,
                timeout_seconds=timeout,
                on_line=on_line,
            )
        )
        artifact = None
        if report_host.exists():
            artifact = str(report_host)
        return RawResult(
            tool="gitleaks",
            target=str(repo_path),
            stdout=result.output,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
            artifact_path=artifact,
        )
    finally:
        pass  # caller reads artifact then removes tempdir
