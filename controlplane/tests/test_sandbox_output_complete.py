"""The sandbox must not truncate a command's output.

The reader loop runs `while proc.poll() is None`, so it stopped the instant
the container exited and discarded whatever was still in the pipe buffer.
Only the timeout path drained it. Any command that writes more than the
buffer holds and then exits promptly lost its tail while the exit code still
reported success.

This is not cosmetic. It is how the pre-deploy vulnerability gate came to pass
an image with a CRITICAL finding: Trivy's 295 KB report arrived truncated,
truncated JSON does not parse, and the parser treated unreadable output as
"no findings".
"""

import json

import pytest
from controlplane.runners.sandbox import SandboxRun, run_sandbox
from controlplane.runners.scanners.base import SANDBOX_IMAGE


@pytest.mark.integration
def test_large_output_survives_a_fast_exit():
    """Emit far more than a pipe buffer, then exit at once."""
    # Built inside the container: the document must be large, and a 400 KB
    # argv would fail before the sandbox was exercised at all.
    # Many lines, not one big one. The reader consumes a line per iteration
    # and stops as soon as the process exits, so the loss only shows up when
    # the writer finishes well ahead of the reader — which is what an
    # indented 300 KB scan report does and a single long line does not.
    program = (
        "import json;"
        "print(json.dumps({'Results': [{'Target': 'pkg-%d' % i, 'n': 'x' * 200}"
        " for i in range(2000)]}, indent=2))"
    )

    result = run_sandbox(
        SandboxRun(
            command=["python3", "-c", program],
            image=SANDBOX_IMAGE,
            network_enabled=False,
            timeout_seconds=120,
        )
    )

    assert result.exit_code == 0, result.output
    assert not result.timed_out

    # The whole document must be there, and still be parseable — a truncated
    # report is exactly what used to read as "clean".
    assert len(result.output) > 400_000, "output too small to prove anything"
    recovered = json.loads(result.output)
    assert len(recovered["Results"]) == 2000, "the report lost entries in transit"
