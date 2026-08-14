"""Gitleaks JSON report -> ParsedFindings.

Gitleaks findings have no native severity; they are mapped to ``high``
(docs/PLATFORM_SPEC.md §5).
"""

import json

from controlplane.parsers import ParsedFindings, bump, empty_summary


def parse_gitleaks(raw_output: str) -> ParsedFindings:
    try:
        data = json.loads(raw_output or "[]")
    except json.JSONDecodeError:
        return ParsedFindings()
    if isinstance(data, dict):
        data = data.get("leaks", [])
    summary = empty_summary()
    findings = []
    for leak in data:
        severity = "high"
        summary = bump(summary, severity)
        findings.append(
            {
                "severity": severity,
                "identifier": leak.get("RuleID"),
                "package_name": None,
                "installed_version": None,
                "fixed_version": None,
                "title": leak.get("Description"),
                "description": None,
                "file_path": leak.get("File"),
                "line_number": leak.get("StartLine"),
            }
        )
    return ParsedFindings(summary=summary, findings=findings)
