"""Trivy JSON -> ParsedFindings."""

import json

from controlplane.parsers import ParsedFindings, bump, empty_summary, normalize_severity


def parse_trivy(raw_output: str) -> ParsedFindings:
    try:
        data = json.loads(raw_output or "{}")
    except json.JSONDecodeError:
        return ParsedFindings()
    summary = empty_summary()
    findings = []
    for result in data.get("Results", []):
        for vuln in result.get("Vulnerabilities", []):
            severity = normalize_severity(vuln.get("Severity"))
            summary = bump(summary, severity)
            findings.append(
                {
                    "severity": severity,
                    "identifier": vuln.get("VulnerabilityID"),
                    "package_name": vuln.get("PkgName"),
                    "installed_version": vuln.get("InstalledVersion"),
                    "fixed_version": vuln.get("FixedVersion") or None,
                    "title": vuln.get("Title"),
                    "description": vuln.get("Description"),
                    "file_path": result.get("Target"),
                    "line_number": None,
                }
            )
    return ParsedFindings(summary=summary, findings=findings)
