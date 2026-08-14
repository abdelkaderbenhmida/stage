"""Parsers: tool JSON -> normalized summary + Finding rows.

Severity normalization to the shared five-value scale
(CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN) happens here.
"""

from dataclasses import dataclass, field

SEVERITY_ORDER = ("critical", "high", "medium", "low", "unknown")


def normalize_severity(severity: str | None) -> str:
    key = (severity or "unknown").lower()
    return key if key in SEVERITY_ORDER else "unknown"


def empty_summary() -> dict:
    return {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}


def bump(summary: dict, severity: str) -> dict:
    key = normalize_severity(severity)
    summary[key] = summary.get(key, 0) + 1
    return summary


@dataclass
class ParsedFindings:
    summary: dict = field(default_factory=empty_summary)
    findings: list[dict] = field(default_factory=list)
