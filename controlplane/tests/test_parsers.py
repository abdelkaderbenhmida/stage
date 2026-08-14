"""Parser tests against real fixture outputs (docs/PLATFORM_SPEC.md §5)."""

import json
from pathlib import Path

from controlplane.parsers.gitleaks_parser import parse_gitleaks
from controlplane.parsers.pip_audit_parser import parse_pip_audit
from controlplane.parsers.trivy_parser import parse_trivy

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_trivy_realistic_report():
    parsed = parse_trivy(_load("trivy_results.json"))
    assert parsed.summary == {"critical": 1, "high": 1, "medium": 1, "low": 0, "unknown": 0}
    assert len(parsed.findings) == 3

    by_id = {f["identifier"]: f for f in parsed.findings}
    assert by_id["CVE-2024-24576"]["severity"] == "critical"
    assert by_id["CVE-2024-24576"]["package_name"] == "jinja2"
    assert by_id["CVE-2024-24576"]["fixed_version"] == "3.1.4"
    assert by_id["CVE-2024-24576"]["file_path"] == "app/users-service"
    assert by_id["CVE-2024-2511"]["severity"] == "medium"


def test_trivy_empty_report():
    parsed = parse_trivy(_load("trivy_empty.json"))
    assert parsed.summary == {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    assert parsed.findings == []


def test_trivy_malformed_json_is_safe():
    parsed = parse_trivy("not json at all")
    assert parsed.findings == []


def test_trivy_empty_string():
    parsed = parse_trivy("")
    assert parsed.findings == []


def test_trivy_unknown_severity_normalized():
    parsed = parse_trivy(
        json.dumps(
            {
                "Results": [
                    {
                        "Target": "img",
                        "Vulnerabilities": [
                            {"VulnerabilityID": "X-1", "Severity": "None", "PkgName": "pkg"}
                        ],
                    }
                ]
            }
        )
    )
    assert parsed.findings[0]["severity"] == "unknown"


def test_gitleaks_realistic_report():
    parsed = parse_gitleaks(_load("gitleaks_report.json"))
    assert parsed.summary["high"] == 2
    assert parsed.findings[0]["identifier"] == "aws-access-token"
    assert parsed.findings[0]["severity"] == "high"
    assert parsed.findings[0]["file_path"] == "config/settings.py"
    assert parsed.findings[0]["line_number"] == 12


def test_gitleaks_empty_report():
    parsed = parse_gitleaks(_load("gitleaks_empty.json"))
    assert parsed.findings == []
    assert parsed.summary["high"] == 0


def test_gitleaks_malformed_json_is_safe():
    parsed = parse_gitleaks("<<< not json")
    assert parsed.findings == []


def test_pip_audit_realistic_report():
    parsed = parse_pip_audit(_load("pip_audit_results.json"))
    # CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N -> base score 9.8 (critical)
    assert parsed.summary == {"critical": 2, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    by_id = {f["identifier"]: f for f in parsed.findings}
    assert by_id["PYSEC-2023-62"]["severity"] == "critical"
    assert by_id["PYSEC-2023-62"]["package_name"] == "flask"
    assert by_id["PYSEC-2023-62"]["fixed_version"] == "2.3.0"
    assert by_id["PYSEC-2023-74"]["severity"] == "critical"
    assert by_id["PYSEC-2023-74"]["installed_version"] == "2.28.0"


def test_pip_audit_empty():
    parsed = parse_pip_audit(_load("pip_audit_empty.json"))
    assert parsed.findings == []


def test_pip_audit_malformed():
    parsed = parse_pip_audit("garbage")
    assert parsed.findings == []
