"""The pre-deploy vulnerability gate must fail closed.

The console tells the user "the image is scanned before it reaches the
cluster; a CRITICAL or HIGH finding blocks the deployment". That promise was
broken by a silent fail-open: `parse_trivy` returns an empty result for output
it cannot read, which is byte-for-byte the same as a clean scan, so a scanner
that never ran produced a gate count of 0 and the image deployed. Observed in
production on this instance — trivy could not reach the Docker socket and an
unscanned image went live.

An image whose vulnerabilities are unknown is not an image known to be safe.
"""

import pytest
from controlplane.parsers.trivy_parser import parse_trivy
from controlplane.workers.tasks import _is_usable_trivy_report


@pytest.mark.parametrize(
    "raw",
    [
        "",
        None,
        "failed to connect to the docker API at unix:///var/run/docker.sock",
        "2026-08-17T12:00:00Z\tFATAL\tunable to inspect the image",
        "null",
        "[]",
        '{"SchemaVersion": 2}',  # valid JSON, but no report in it
    ],
)
def test_unusable_scanner_output_is_not_mistaken_for_a_clean_image(raw):
    """Each of these must be rejected, and each parses to zero findings."""
    assert _is_usable_trivy_report(raw) is False

    # The point of the guard: the parser cannot tell these apart from clean.
    parsed = parse_trivy(raw or "")
    gate = parsed.summary.get("critical", 0) + parsed.summary.get("high", 0)
    assert gate == 0, "parser reports clean, so only the guard can block this"


def test_a_real_empty_report_is_accepted():
    """A genuine scan that found nothing must still be allowed to deploy."""
    assert _is_usable_trivy_report('{"SchemaVersion": 2, "Results": []}') is True


def test_a_real_report_with_findings_is_accepted_and_counted():
    raw = """
    {"SchemaVersion": 2, "Results": [
      {"Target": "img", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-1", "Severity": "CRITICAL", "PkgName": "p"},
        {"VulnerabilityID": "CVE-2", "Severity": "HIGH", "PkgName": "q"}
      ]}
    ]}
    """
    assert _is_usable_trivy_report(raw) is True
    parsed = parse_trivy(raw)
    assert parsed.summary["critical"] == 1
    assert parsed.summary["high"] == 1


# --- the sandbox merges stderr into stdout -----------------------------------

from controlplane.runners.scanners.trivy import _json_document  # noqa: E402


def test_report_is_recovered_from_a_stream_that_also_carries_log_lines():
    """Trivy logs to stderr and the sandbox folds stderr into stdout.

    The merged text does not parse as JSON, and an unparseable report used to
    mean "no findings", so the gate passed any image whose scan happened to
    log a warning. That is the same fail-open as a crashed scanner, reached by
    a much more ordinary route.
    """
    noisy = (
        "2026-08-17T13:00:00Z\tINFO\tVulnerability scanning is enabled\n"
        "2026-08-17T13:00:01Z\tWARN\tThis OS version is no longer supported\n"
        '{"SchemaVersion": 2, "Results": [{"Target": "img", "Vulnerabilities": '
        '[{"VulnerabilityID": "CVE-9", "Severity": "CRITICAL", "PkgName": "p"}]}]}\n'
    )
    recovered = _json_document(noisy)
    assert _is_usable_trivy_report(recovered)

    parsed = parse_trivy(recovered)
    assert parsed.summary["critical"] == 1, "a critical finding must survive extraction"


def test_output_with_no_report_is_left_alone():
    """A genuine failure must still look like a failure, not an empty report."""
    failure = "FATAL\tunable to inspect the image: no such image"
    assert _json_document(failure) == failure
    assert _is_usable_trivy_report(_json_document(failure)) is False
