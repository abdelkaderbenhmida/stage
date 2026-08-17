"""What the clone must leave behind on the host, and what it must not.

The sandbox runs containers as uid 0 while the control plane runs
unprivileged, so everything a clone writes is owned by root. Cleanup that
used `shutil.rmtree(..., ignore_errors=True)` could not remove those files and
said nothing about it. Two consequences were live on this instance:

  * 4.8 MB of tenant checkouts across 49 directories accumulated in /tmp;
  * `.git` survived into the docker build context, so a tenant Dockerfile
    doing `COPY . .` would copy the remote configuration — and, for an
    authenticated clone, the credential used to fetch it — into an image that
    is then pushed to a registry.
"""

import uuid
from pathlib import Path

import pytest
from controlplane.workers import tasks

PUBLIC_REPO = "https://github.com/octocat/Hello-World.git"


@pytest.fixture
def _quiet_logs(monkeypatch):
    """The job ids here are not real Job rows."""
    monkeypatch.setattr(tasks, "_append_log", lambda *a, **k: None)


@pytest.mark.integration
def test_git_metadata_never_reaches_the_build_context(_quiet_logs):
    repo = tasks._clone_repo(PUBLIC_REPO, str(uuid.uuid4()), None, branch="master")
    try:
        assert repo.exists(), "clone produced nothing"
        assert not (repo / ".git").exists(), ".git survived into the build context"
        # The askpass helper must not linger either.
        assert not (repo.parent / "askpass.sh").exists()
    finally:
        tasks._purge_path(repo.parent)


@pytest.mark.integration
def test_a_root_owned_checkout_can_actually_be_removed(_quiet_logs):
    """The leak was silent, so assert on the filesystem, not the return value."""
    repo = tasks._clone_repo(PUBLIC_REPO, str(uuid.uuid4()), None, branch="master")
    parent = repo.parent

    assert tasks._purge_path(parent) is True
    assert not parent.exists(), "tenant checkout outlived the job"


@pytest.mark.integration
def test_purge_reports_failure_rather_than_swallowing_it(_quiet_logs, monkeypatch):
    """A cleanup that cannot complete must say so; that is what was missing."""
    target = Path("/tmp") / f"ctl-purge-test-{uuid.uuid4().hex}"
    (target / "inner").mkdir(parents=True)

    # Make the container removal a no-op so the path survives.
    monkeypatch.setattr(tasks, "run_sandbox", lambda spec: None)
    monkeypatch.setattr(tasks.shutil, "rmtree", lambda *a, **k: None)

    assert tasks._purge_path(target) is False, "a surviving path must be reported"

    # Real cleanup for the test's own mess.
    monkeypatch.undo()
    tasks._purge_path(target)


def test_purge_of_a_missing_path_is_a_success():
    assert tasks._purge_path(Path("/tmp") / f"never-existed-{uuid.uuid4().hex}") is True
