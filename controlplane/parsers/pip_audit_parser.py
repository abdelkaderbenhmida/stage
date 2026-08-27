"""pip-audit JSON -> ParsedFindings.

Severity may be a plain string (``CRITICAL``) or a CVSS detail dict; both are
normalized to the shared scale.
"""

import json

from controlplane.parsers import ParsedFindings, bump, empty_summary, normalize_severity


def _cvss3_base_score(vector: str) -> float:
    """Estimate the CVSS 3.1 base score from a vector string."""
    metrics = dict(
        item.split(":", 1) if ":" in item else (item, "")
        for item in vector.upper().split("/")
        if item
    )
    av = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}.get(metrics.get("AV", ""), 0.2)
    ac = {"L": 0.77, "H": 0.44}.get(metrics.get("AC", ""), 0.44)
    pr = {"N": 0.85, "L": 0.62, "H": 0.27}.get(metrics.get("PR", ""), 0.27)
    ui = {"N": 0.85, "R": 0.62}.get(metrics.get("UI", ""), 0.62)
    c = {"H": 0.56, "L": 0.22, "N": 0.0}.get(metrics.get("C", ""), 0.0)
    i = {"H": 0.56, "L": 0.22, "N": 0.0}.get(metrics.get("I", ""), 0.0)
    a = {"H": 0.56, "L": 0.22, "N": 0.0}.get(metrics.get("A", ""), 0.0)

    scope = metrics.get("S", "U") == "C"
    impact = 1 - (1 - c) * (1 - i) * (1 - a)
    if scope:
        impact = 7.52 * (impact - 0.029) - 3.25 * (impact - 0.02) ** 15
    else:
        impact = 6.42 * impact
    exploitability = 8.22 * av * ac * pr * ui
    base = round(min(impact + exploitability, 10.0), 1) if scope else round(min(1.08 * (impact + exploitability), 10.0), 1)
    return base


def _score_to_level(score: float) -> str:
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0.0:
        return "low"
    return "unknown"


def _severity_of(vuln: dict) -> str:
    raw = vuln.get("severity")
    if isinstance(raw, dict):
        cvss = raw.get("cvssV3") or {}
        vector = cvss.get("vectorString", "")
        if vector:
            return _score_to_level(_cvss3_base_score(vector))
        score = raw.get("score")
        if isinstance(score, int | float):
            return _score_to_level(float(score))
        return normalize_severity(score)
    return normalize_severity(raw)


def parse_pip_audit(raw_output: str) -> ParsedFindings:
    try:
        data = json.loads(raw_output or "{}")
    except json.JSONDecodeError:
        return ParsedFindings()
    summary = empty_summary()
    findings = []
    # pip-audit resolves each dependency against several advisory sources, and
    # the same advisory reached through two of them is reported twice with
    # different prose. Counting both made one vulnerable urllib3 pin show as
    # 12 findings where there are 9, and the inflated number is what the
    # Security page's severity tiles displayed.
    seen: set[tuple[str, str]] = set()
    for dep in data.get("dependencies", []):
        name = dep.get("name")
        version = dep.get("version")
        for vuln in dep.get("vulns", []):
            key = (str(name), str(vuln.get("id")))
            if key in seen:
                continue
            seen.add(key)
            severity = _severity_of(vuln)
            summary = bump(summary, severity)
            fix_versions = vuln.get("fix_versions") or []
            # pip-audit carries no severity of its own, so a PYSEC id is all
            # these rows would otherwise show. The aliases are what a reader
            # can actually look up — CVE-2021-33503 says more than
            # PYSEC-2021-108 to everyone who does not live in OSV.
            aliases = [a for a in (vuln.get("aliases") or []) if a]
            findings.append(
                {
                    "severity": severity,
                    "identifier": vuln.get("id"),
                    "package_name": name,
                    "installed_version": version,
                    "fixed_version": fix_versions[0] if fix_versions else None,
                    "title": ", ".join(aliases) or None,
                    "description": vuln.get("description"),
                    "file_path": None,
                    "line_number": None,
                }
            )
    return ParsedFindings(summary=summary, findings=findings)
