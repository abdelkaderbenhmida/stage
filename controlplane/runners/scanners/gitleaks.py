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
    report_dir = Path(tmpdir)
    report_host = report_dir / "report.json"
    # The mount is the DIRECTORY, not the file. A single-file bind mount is
    # fragile in two independent ways, both observed live: if the file does
    # not exist yet, Docker either refuses the mount outright or silently
    # creates a directory at that path instead; and even a pre-created file
    # can be unwritable from inside the container — reproduced here even
    # with world-writable permissions and true root, on plain `alpine`, with
    # no relation to this image or this code. Writing INTO an existing,
    # writable directory has neither failure mode, so gitleaks is given the
    # directory and told where in it to put the report — the same shape
    # trivy already uses for its cache (a mounted directory it writes files
    # into), which is why that scanner never hit this.
    try:
        result = run_sandbox(
            SandboxRun(
                command=[
                    "gitleaks", "detect",
                    "--source", str(repo_path),
                    "--report-format", "json",
                    "--report-path", "/tmp/out/report.json",
                    "--no-banner",
                    # Without this, `detect` tries to scan git HISTORY, and
                    # repo_path never has one: _clone_repo removes .git
                    # before any pipeline step runs, on purpose, so a
                    # tenant's Dockerfile `COPY . .` cannot bake it into an
                    # image. Missing .git makes git log discovery fail with
                    # "not a git repository", which gitleaks logs as an
                    # error and then reports as "no leaks found" — a scan
                    # that never ran, indistinguishable from a clean one.
                    # The gate passed every checkout unconditionally,
                    # silently, until this was caught by actually running a
                    # deploy instead of a stubbed one. --no-git scans the
                    # files on disk instead of git history, which is the
                    # only thing there ever was to scan here.
                    "--no-git",
                    # A bare flag, not "--redact true": gitleaks' --redact
                    # takes an OPTIONAL uint (percent, default 100 when the
                    # flag is present with no value) — "true" is not a valid
                    # uint, and gitleaks exits with "invalid argument \"true\"
                    # for \"--redact\" flag" before it scans anything.
                    #
                    # Every finding gitleaks reports is a committed secret, so
                    # printing an unredacted one to a job log the tenant's
                    # whole team can read is exactly the leak this scan exists
                    # to catch — redaction is not optional here.
                    "--redact",
                ],
                image=SANDBOX_IMAGE,
                workspace=repo_path,
                writable_paths=[],
                mounts=[(report_dir, "/tmp/out", False)],
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
