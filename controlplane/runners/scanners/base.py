from collections.abc import Callable
from dataclasses import dataclass


class ScannerError(RuntimeError):
    pass


@dataclass
class RawResult:
    """Uniform output of a scanner run (docs/PLATFORM_SPEC.md §5)."""

    tool: str
    target: str
    stdout: str = ""
    exit_code: int = 0
    timed_out: bool = False
    duration_seconds: float = 0.0
    artifact_path: str | None = None  # e.g. gitleaks report file


# Tool image tags — the sandbox image bundles all four tools.
SANDBOX_IMAGE: str = "platform/sandbox:latest"


def _line_cb(on_line: Callable[[str], None] | None) -> Callable[[str], None] | None:
    return on_line
