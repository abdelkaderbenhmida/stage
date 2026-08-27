"""Secret scrubbing for job logs (docs/PLATFORM_SPEC.md §7.4).

Any line matching a known secret pattern is replaced with ``[REDACTED]``
before the log is written to the database.
"""

import re

_REDACTED = "[REDACTED]"

# High-confidence patterns: these shapes are secrets and nothing else, so the
# allow-list below must never exempt them. Keeping them separate matters —
# the allow-list works on whole lines, and a tenant chooses their own
# repository name, so a repository called "example.com" could otherwise switch
# scrubbing off for every line that mentions it.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    # GitHub tokens. ghs_ is an App installation token and ghu_/ghr_ are the
    # user-to-server and refresh forms; the previous pattern matched only
    # ghp_ and a bare "gho_" prefix, so the very token type this platform
    # prefers for private clones was not covered.
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{16,}"),  # GitLab personal access token
    # A credential embedded in a URL, which is how a token most often escapes:
    # git echoes the remote back in its error messages.
    re.compile(r"://[^/\s:@]+:[^/\s@]+@"),
]

# Heuristic patterns: useful, but they match ordinary prose too, so the
# allow-list applies to these only.
_HEURISTIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|secret[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"sshpass\s+-p\s+['\"]?\S+"),
]

# Contexts that are NOT secrets (e.g. password= hashing refs in docs).
_OK_SUBSTRINGS = (
    "password_hash",
    "hashed_password",
    "example.com",
)


def _looks_safe(line: str) -> bool:
    lowered = line.lower()
    return any(sub in lowered for sub in _OK_SUBSTRINGS)


# Colour codes from any tool that thinks it is writing to a terminal. Job logs
# are read in the browser, where they render as literal "[90m" fragments —
# gitleaks' own summary reached the deploy log looking like
# `\x1b[90m7:58PM\x1b[0m \x1b[32mINF\x1b[0m no leaks found`. Stripped here
# because this is the one funnel every streamed sandbox line passes through
# (runners/sandbox.py); _run() does the same for captured output.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def scrub_line(line: str) -> str:
    """Return ``line`` with any matched secret replaced by ``[REDACTED]``."""
    line = _ANSI_RE.sub("", line)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(line):
            return _REDACTED
    if _looks_safe(line):
        return line
    for pattern in _HEURISTIC_PATTERNS:
        if pattern.search(line):
            return _REDACTED
    return line


def scrub_stream(lines):
    """Generator: consume raw log lines, yield scrubbed lines."""
    for line in lines:
        yield scrub_line(line.rstrip("\n"))
