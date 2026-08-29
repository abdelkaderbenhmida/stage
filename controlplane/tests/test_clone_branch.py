"""A deployment must build the branch it names.

`_clone_repo` issued a bare `git clone --depth 1 <url> <dir>`, so the branch
recorded on the Deployment was never passed to git. A service pinned to
"develop" was built from the repository's default branch and then labelled
develop everywhere in the UI — the platform shipped code the user had not
asked for and reported success. These tests pin the flag, the credential
prompt, and the actionable failure text.
"""


import pytest
from controlplane.workers import tasks


class _Result:
    def __init__(self, exit_code=0, output="", timed_out=False):
        self.exit_code = exit_code
        self.output = output
        self.timed_out = timed_out
        self.stdout = output


def _capture(monkeypatch):
    seen = {}

    def _run(spec):
        seen["command"] = spec.command
        seen["env"] = getattr(spec, "env", None)
        return _Result()

    monkeypatch.setattr(tasks, "run_sandbox", _run)
    monkeypatch.setattr(tasks, "_append_log", lambda *a, **k: None)
    return seen


def test_clone_passes_the_requested_branch_to_git(monkeypatch):
    seen = _capture(monkeypatch)
    tasks._clone_repo("https://github.com/org/repo.git", "job1", None, branch="develop")

    command = seen["command"]
    assert "--branch" in command, f"branch never reaches git: {command}"
    assert command[command.index("--branch") + 1] == "develop"


def test_clone_without_a_branch_omits_the_flag(monkeypatch):
    """Scans clone a bare URL with no branch; git must pick the default."""
    seen = _capture(monkeypatch)
    tasks._clone_repo("https://github.com/org/repo.git", "job1", None)
    assert "--branch" not in seen["command"]


def test_clone_never_prompts_for_credentials(monkeypatch):
    """A private repo must fail immediately, not block on stdin until timeout."""
    seen = _capture(monkeypatch)
    tasks._clone_repo("https://github.com/org/repo.git", "job1", None, branch="main")
    assert (seen["env"] or {}).get("GIT_TERMINAL_PROMPT") == "0"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("fatal: could not read Username for 'https://github.com'", "private or does not exist"),
        ("fatal: Remote branch nope not found in upstream origin", "does not exist in"),
    ],
)
def test_clone_failures_explain_themselves(output, expected):
    hint = tasks._clone_hint(_Result(exit_code=128, output=output), "https://github.com/o/r.git", "nope")
    assert hint and expected in hint

