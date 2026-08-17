"""Destroying a project must never delete anything outside its workspace.

`queue_destroy` passes `workspace or ""`, so a project whose `workspace_path`
was never set — a draft, or one adopted from the warm pool — reached
`shutil.rmtree(Path(""), ignore_errors=True)`. `Path("")` is `.`, so that
deleted the worker's current working directory, and `ignore_errors=True` meant
it did so in silence.

This is not hypothetical. It repeatedly destroyed this repository during full
test runs: `test_tasks.py` calls `destroy_task(..., "", ...)` with an empty
workspace, and the working directory at the time was the checkout.
"""

import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from controlplane.workers import tasks


@pytest.fixture
def _quiet(monkeypatch):
    monkeypatch.setattr(tasks, "_append_log", lambda *a, **k: None)


@pytest.mark.parametrize("unsafe", ["", "   ", ".", "..", "/", "/etc", "relative/path"])
def test_a_workspace_outside_the_root_is_never_removed(_quiet, monkeypatch, tmp_path, unsafe):
    """Each of these used to be handed straight to rmtree."""
    # settings is a frozen dataclass; swap the module's reference to it.
    monkeypatch.setattr(tasks, "settings", SimpleNamespace(workspace_root=str(tmp_path)))

    removed: list = []
    monkeypatch.setattr(tasks.shutil, "rmtree", lambda p, **k: removed.append(p))

    tasks._remove_workspace(Path(unsafe), "job-1")

    assert removed == [], f"{unsafe!r} was passed to rmtree"


def test_the_empty_workspace_does_not_delete_the_working_directory(_quiet, monkeypatch, tmp_path):
    """The exact failure: Path("") resolves to the process's cwd."""
    # settings is a frozen dataclass; swap the module's reference to it.
    monkeypatch.setattr(tasks, "settings", SimpleNamespace(workspace_root=str(tmp_path)))

    cwd = Path.cwd().resolve()
    assert Path("").resolve() == cwd, "precondition: empty path is the cwd"

    removed: list = []
    monkeypatch.setattr(tasks.shutil, "rmtree", lambda p, **k: removed.append(Path(p).resolve()))

    tasks._remove_workspace(Path(""), "job-1")

    assert cwd not in removed, "destroy deleted the working directory"


def test_a_real_workspace_is_still_removed(_quiet, monkeypatch, tmp_path):
    """The guard must not break the thing it is guarding."""
    # settings is a frozen dataclass; swap the module's reference to it.
    monkeypatch.setattr(tasks, "settings", SimpleNamespace(workspace_root=str(tmp_path)))

    workspace = tmp_path / f"project-{uuid.uuid4().hex}"
    (workspace / "inner").mkdir(parents=True)
    (workspace / "inner" / "terraform.tfstate").write_text("{}")

    tasks._remove_workspace(workspace, "job-1")

    assert not workspace.exists(), "a legitimate workspace was left behind"


def test_the_workspace_root_itself_is_not_removable(_quiet, monkeypatch, tmp_path):
    """Deleting the root would take every other tenant's workspace with it."""
    # settings is a frozen dataclass; swap the module's reference to it.
    monkeypatch.setattr(tasks, "settings", SimpleNamespace(workspace_root=str(tmp_path)))
    (tmp_path / "someone-elses-project").mkdir()

    tasks._remove_workspace(tmp_path, "job-1")

    assert tmp_path.exists()
    assert (tmp_path / "someone-elses-project").exists()


def test_a_symlink_out_of_the_root_is_refused(_quiet, monkeypatch, tmp_path):
    """resolve() is what makes the containment check meaningful."""
    # settings is a frozen dataclass; swap the module's reference to it.
    monkeypatch.setattr(tasks, "settings", SimpleNamespace(workspace_root=str(tmp_path)))

    outside = tmp_path.parent / f"outside-{uuid.uuid4().hex}"
    outside.mkdir()
    (outside / "keep.txt").write_text("important")
    link = tmp_path / "escape"
    os.symlink(outside, link)

    tasks._remove_workspace(link, "job-1")

    assert (outside / "keep.txt").exists(), "followed a symlink out of the workspace root"
