"""Secret scrubbing for job logs (docs/PLATFORM_SPEC.md §7.4).

Any line matching a known secret pattern is replaced with ``[REDACTED]``
before the log is written to the database.
"""

import re

_REDACTED = "[REDACTED]"

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|secret[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"sshpass\s+-p\s+['\"]?\S+"),
    re.compile(r"(?i)(ghp_[A-Za-z0-9]{36}|gho_|github_pat_[A-Za-z0-9_]{22,})"),
]

# Allow-list contexts that are NOT secrets (e.g. password= hashing refs in docs)
_OK_SUBSTRINGS = (
    "password_hash",
    "hashed_password",
    "example.com",
)


def _looks_safe(line: str) -> bool:
    lowered = line.lower()
    return any(sub in lowered for sub in _OK_SUBSTRINGS)


def scrub_line(line: str) -> str:
    """Return ``line`` with any matched secret replaced by ``[REDACTED]]``."""
    if _looks_safe(line):
        return line
    result = line
    for pattern in _PATTERNS:
        if pattern.search(result):
            result = _REDACTED
            break
    return result


def scrub_stream(lines):
    """Generator: consume raw log lines, yield scrubbed lines."""
    for line in lines:
        yield scrub_line(line.rstrip("\n"))
