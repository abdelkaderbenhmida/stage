"""Scanner runners against a real sandbox container, not a stub.

Every other test that exercises deploy_task or scan_task stubs run_gitleaks
entirely, which is right for testing the pipeline's control flow — but it
also means nothing ever ran gitleaks.detect's real Docker invocation until a
live deploy did, and that invocation had never worked: Docker either refused
the bind mount outright ("bind source path does not exist") or silently
created a directory at the mount target instead of a file, and either way
gitleaks could not write its report. Every real deploy through the sandbox
path failed at the secret-scan gate, always, and it read exactly like a
scan that had found something.

These tests run the actual scanner against the actual sandbox image, the
same one deploy_task uses, so the same class of bug cannot hide behind a
stub again.
"""

import subprocess
from pathlib import Path

import pytest
from controlplane.runners.scanners.gitleaks import run_gitleaks

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_docker = pytest.mark.skipif(not _docker_available(), reason="docker not available")


@requires_docker
def test_gitleaks_runs_against_a_clean_checkout(tmp_path, session):
    # `session` is unused directly — requested so the shared `_clean_db`
    # autouse fixture (conftest.py) has a real, testcontainers-backed engine
    # to tear down. Every test in this module carries the `integration`
    # marker to stay out of the Docker-free default gate; in this repo that
    # marker specifically means "a real Postgres via testcontainers", and the
    # teardown assumes one exists for any test carrying it.
    """The regression this guards: report_host was a calculated path that
    never touched the filesystem, so Docker's bind-mount source did not exist
    when the container started. touch() before mounting is the fix; this
    proves the whole real invocation completes rather than only asserting the
    one line that changed."""
    (tmp_path / "main.py").write_text("print('hello')\n")

    result = run_gitleaks(tmp_path, timeout=60)

    assert not result.timed_out
    # 0 = clean scan, 1 = leaks found — both mean gitleaks ran to completion.
    # Anything else (125 = docker rejected the mount, 126/127 = exec failure)
    # is the bug this test exists to catch.
    assert result.exit_code in (0, 1), (
        f"gitleaks did not run to completion (exit {result.exit_code}): {result.stdout[-500:]}"
    )
    assert result.artifact_path is not None, "no report file was produced"


@requires_docker
def test_gitleaks_report_is_a_file_not_a_directory(tmp_path, session):
    """The exact failure mode observed live: Docker silently created a
    directory at the bind-mount target because the host source path did not
    exist yet, and gitleaks could not open a directory for writing."""
    (tmp_path / "main.py").write_text("print('hello')\n")

    result = run_gitleaks(tmp_path, timeout=60)

    assert result.artifact_path is not None
    assert Path(result.artifact_path).is_file(), "report path is a directory, not a file"


@requires_docker
def test_gitleaks_finds_a_real_committed_secret(tmp_path, session):
    """Round-trip through the real parser too: a finding must actually reach
    the caller, not just "the container ran"."""
    # Not AWS's own AKIAIOSFODNN7EXAMPLE — that exact string is gitleaks'
    # default allowlist, since it is AWS's own documentation placeholder and
    # appears in every SDK example ever written. A generic high-entropy
    # token pattern triggers the generic-api-key rule instead.
    (tmp_path / "config.py").write_text(
        'STRIPE_API_KEY = "sk_live_4eC39HqLyjWDarjtT1zdp7dc"\n'
    )

    result = run_gitleaks(tmp_path, timeout=60)

    assert result.exit_code == 1, "gitleaks should report leaks found"
    from controlplane.parsers.gitleaks_parser import parse_gitleaks

    report_text = Path(result.artifact_path).read_text()
    parsed = parse_gitleaks(report_text)
    assert sum(parsed.summary.values()) > 0, "the committed secret was not reported"
