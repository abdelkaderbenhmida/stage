"""Transient tenant checkouts must not survive a failed job.

A deploy or scan clones the tenant's repository onto the shared control-plane
host. Cleanup used to sit inline after the build step, so any failure between
the clone and that line left the checkout on disk permanently — unbounded /tmp
growth, and one tenant's source lingering on shared infrastructure after a
failure they never saw. These tests pin the cleanup to the failure path.
"""

import ast
from pathlib import Path

import pytest

TASKS = Path(__file__).resolve().parents[1] / "workers" / "tasks.py"


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(TASKS.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {TASKS}")


def _rmtree_calls(node: ast.AST) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "rmtree"
    ]


@pytest.mark.parametrize("task", ["scan_task", "deploy_task"])
def test_clone_cleanup_runs_on_the_failure_path(task):
    """Every rmtree of a clone must sit in a `finally`, not inline."""
    fn = _function(task)

    in_finally = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            for stmt in node.finalbody:
                in_finally.extend(_rmtree_calls(stmt))

    all_calls = _rmtree_calls(fn)
    assert all_calls, f"{task} no longer cleans up its clone at all"

    inline = [c for c in all_calls if c not in in_finally]
    assert not inline, (
        f"{task} removes a clone outside `finally` (line(s) "
        f"{[c.lineno for c in inline]}): a failure before that line leaks "
        "tenant source onto the control-plane host"
    )
