"""Pipeline graph: status mapping, CI DAG layering, cycle guard, matrix collapse.

Pure unit tests (no DB): the status mapping table, the matrix rollup, the
workflow-file DAG shape, the frontend layout function (run through Node.js,
which is only used for layout — no DOM), and ci_run_graph() with `_run`
monkeypatched so no gh/git subprocess ever fires.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from controlplane import platform_ops

PLATFORM_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "pipeline-graph.js"


# ---------------------------------------------------------------------------
# Status mapping table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gh_status", "gh_conclusion", "expected"),
    [
        ("completed", "success", "succeeded"),
        ("completed", "failure", "failed"),
        ("completed", "cancelled", "cancelled"),
        ("completed", "skipped", "skipped"),
        ("completed", "interrupted", "failed"),  # cancelled mid-run → failed
        ("completed", None, "skipped"),  # stopped run with no conclusion
        ("in_progress", None, "running"),
        ("queued", None, "queued"),
        ("waiting", None, "queued"),
        ("requested", None, "queued"),
        ("pending", None, "queued"),
        ("weird", None, "skipped"),  # unknown → skipped
        (None, None, "skipped"),
    ],
)
def test_ci_job_status_mapping(gh_status, gh_conclusion, expected):
    job = {"status": gh_status, "conclusion": gh_conclusion}
    assert platform_ops._ci_job_status(job) == expected


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["succeeded"], "succeeded"),
        (["failed", "succeeded"], "failed"),  # any failure → failed
        (["running", "succeeded"], "running"),  # else any running → running
        (["queued", "succeeded"], "queued"),  # else any queued → queued
        (["succeeded", "succeeded"], "succeeded"),  # else all success → succeeded
        (["succeeded", "cancelled"], "cancelled"),
        (["succeeded", "skipped"], "skipped"),
        ([], "skipped"),
    ],
)
def test_rollup_statuses(statuses, expected):
    assert platform_ops._rollup_statuses(statuses) == expected


# ---------------------------------------------------------------------------
# CI DAG layering from the real workflow file
# ---------------------------------------------------------------------------


def test_ci_dag_layering_from_workflow_file():
    ci = platform_ops.parse_ci()
    job_ids = {j["id"] for j in ci["jobs"]}
    assert "discover" in job_ids and "lint" in job_ids and "gitleaks" in job_ids
    assert "test" in job_ids and "deploy" in job_ids
    assert ci["discovery"]["id"] == "discover"

    needs = {j["id"]: j["needs"] for j in ci["jobs"]}
    # discover is the root: it depends on nothing and feeds the chain.
    assert needs["discover"] == []
    assert any("discover" in needs[j] for j in job_ids if j != "discover")

    # test blocks on lint + gitleaks (secrets scan) per the workflow.
    assert "lint" in needs["test"] and "gitleaks" in needs["test"]

    # deploy depends on build + trivy-scan + terraform-validate (which all
    # chain back to discover), and nothing depends on deploy.
    deploy_needs = set(needs["deploy"])
    assert {"build", "trivy-scan", "terraform-validate", "discover"} <= deploy_needs
    assert all("deploy" not in needs[j] for j in job_ids)

    # Every edge references real jobs.
    for e in ci["edges"]:
        assert e["from"] in job_ids and e["to"] in job_ids


def test_ci_dag_is_acyclic_and_topologically_orderable():
    ci = platform_ops.parse_ci()
    indegree = {j["id"]: 0 for j in ci["jobs"]}
    out = {j["id"]: [] for j in ci["jobs"]}
    for e in ci["edges"]:
        out[e["from"]].append(e["to"])
        indegree[e["to"]] += 1
    from collections import deque

    queue = deque(j for j, d in indegree.items() if d == 0)
    order = []
    while queue:
        u = queue.popleft()
        order.append(u)
        for v in out[u]:
            indegree[v] -= 1
            if indegree[v] == 0:
                queue.append(v)
    assert len(order) == len(indegree)  # no cycle
    assert order[0] == "discover"
    assert order.index("deploy") > order.index("build")
    assert order.index("test") > order.index("lint")


# ---------------------------------------------------------------------------
# Frontend layout function via Node.js (pure, no DOM)
# ---------------------------------------------------------------------------


def _layout_via_node(graph: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    harness = (
        "const fs = require('fs'); globalThis.window = {};\n"
        f"require({str(PLATFORM_JS)!r});\n"
        "const graph = JSON.parse(fs.readFileSync(0, 'utf8'));\n"
        "const out = window.PipelineGraph.layout(graph);\n"
        "console.log(JSON.stringify({\n"
        "  nodes: out.nodes.map(n => ({id: n.id, layer: n.layer, row: n.row, detail: n.detail})),\n"
        "  edges: out.edges, width: out.width, height: out.height\n"
        "}));\n"
    )
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps(graph),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"node layout harness failed: {result.stderr}")
    return json.loads(result.stdout)


def test_js_layout_assigns_longest_path_layers():
    laid = _layout_via_node(
        {
            "title": "ci",
            "nodes": [
                {"id": "discover", "name": "discover", "status": "succeeded"},
                {"id": "lint", "name": "lint", "status": "succeeded"},
                {"id": "gitleaks", "name": "gitleaks", "status": "succeeded"},
                {"id": "test", "name": "test", "status": "failed"},
                {"id": "deploy", "name": "deploy", "status": "queued"},
            ],
            "edges": [
                {"from": "discover", "to": "lint"},
                {"from": "discover", "to": "gitleaks"},
                {"from": "lint", "to": "test"},
                {"from": "gitleaks", "to": "test"},
                {"from": "test", "to": "deploy"},
            ],
        }
    )
    layers = {n["id"]: n["layer"] for n in laid["nodes"]}
    assert layers["discover"] == 0
    assert layers["lint"] == 1 and layers["gitleaks"] == 1
    assert layers["test"] == 2
    assert layers["deploy"] == 3
    # All edges point forward in layer space.
    for e in laid["edges"]:
        assert layers[e["from"]] < layers[e["to"]]
    assert len(laid["edges"]) == 5


def test_js_layout_cycle_guard_terminates():
    laid = _layout_via_node(
        {
            "title": "cycle",
            "nodes": [
                {"id": "a", "name": "a", "status": "queued"},
                {"id": "b", "name": "b", "status": "queued"},
                {"id": "c", "name": "c", "status": "queued"},
            ],
            "edges": [
                {"from": "a", "to": "b"},
                {"from": "b", "to": "c"},
                {"from": "c", "to": "a"},
            ],
        }
    )
    # Kahn leaves the cycle behind; the guard must push it into a final layer
    # instead of hanging or dropping nodes.
    assert len(laid["nodes"]) == 3
    max_layer = max(n["layer"] for n in laid["nodes"])
    for n in laid["nodes"]:
        assert n["layer"] == max_layer
        assert "dependency cycle" in (n["detail"] or "")


def test_js_layout_single_node_and_empty_edges():
    laid = _layout_via_node(
        {
            "title": "solo",
            "nodes": [{"id": "job", "name": "deploy", "status": "running"}],
            "edges": [],
        }
    )
    assert [n["id"] for n in laid["nodes"]] == ["job"]
    assert laid["edges"] == []
    assert laid["width"] > 0 and laid["height"] > 0


# ---------------------------------------------------------------------------
# ci_run_graph: join by name, matrix collapse, degraded path
# ---------------------------------------------------------------------------


def _canned_run_view(jobs: list[dict]) -> dict:
    return {"ok": True, "stdout": json.dumps({"displayTitle": "ci: main", "jobs": jobs}), "stderr": "", "code": 0}


@pytest.fixture()
def fake_gh(monkeypatch):
    """Point _repo_slug at a stable value and stub _run to return canned gh output."""

    def _set(payload: dict):
        monkeypatch.setattr(platform_ops, "_repo_slug", lambda: "acme/stage")

        def _fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "gh":
                return payload
            return {"ok": True, "stdout": "origin https://github.com/acme/stage.git", "stderr": "", "code": 0}

        monkeypatch.setattr(platform_ops, "_run", _fake_run)

    return _set


def test_ci_run_graph_joins_workflow_jobs_by_name(fake_gh):
    # Canned gh run view with display names matching the workflow's `name:`.
    jobs = [
        {"databaseId": "101", "name": "Discover services", "status": "completed", "conclusion": "success"},
        {"databaseId": "102", "name": "Lint", "status": "completed", "conclusion": "success"},
        {"databaseId": "103", "name": "Tests + Dependency Audit", "status": "completed", "conclusion": "failure"},
    ]
    fake_gh(_canned_run_view(jobs))
    graph = platform_ops.ci_run_graph("42")
    assert graph["reachable"] is True
    assert graph["title"] == "ci: main"
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["discover"]["status"] == "succeeded"
    assert by_id["test"]["status"] == "failed"
    assert by_id["gitleaks"]["status"] == "skipped"  # no gh job → skipped
    assert by_id["gitleaks"]["detail"] == "no matching job in run"
    # DAG comes from the workflow file, not from GitHub.
    assert any(e["to"] == "test" for e in graph["edges"])


def test_ci_run_graph_matrix_collapse(fake_gh):
    jobs = [
        {"databaseId": "201", "name": "Tests + Dependency Audit (a)", "status": "completed", "conclusion": "success"},
        {"databaseId": "202", "name": "Tests + Dependency Audit (b)", "status": "completed", "conclusion": "failure"},
        {"databaseId": "203", "name": "Tests + Dependency Audit (c)", "status": "completed", "conclusion": "success"},
    ]
    fake_gh(_canned_run_view(jobs))
    graph = platform_ops.ci_run_graph("42")
    test_node = next(n for n in graph["nodes"] if n["id"] == "test")
    assert test_node["status"] == "failed"  # any failure → failed
    assert test_node["detail"] == "3 matrix legs"

    # All legs green → succeeded.
    fake_gh(_canned_run_view([{**j, "conclusion": "success"} for j in jobs]))
    graph = platform_ops.ci_run_graph("42")
    test_node = next(n for n in graph["nodes"] if n["id"] == "test")
    assert test_node["status"] == "succeeded"

    # A leg still running → running.
    fake_gh(_canned_run_view([{**j, "conclusion": "success"} for j in jobs[:2]] + [
        {"databaseId": "202", "name": "Tests + Dependency Audit (b)", "status": "in_progress", "conclusion": None}
    ]))
    graph = platform_ops.ci_run_graph("42")
    test_node = next(n for n in graph["nodes"] if n["id"] == "test")
    assert test_node["status"] == "running"


def test_ci_run_graph_degraded_keeps_shape(fake_gh):
    fake_gh({"ok": False, "stdout": "", "stderr": "gh not installed", "code": -1})
    graph = platform_ops.ci_run_graph("42")
    assert graph["reachable"] is False
    assert "gh not installed" in graph["error"]
    assert graph["nodes"], "degraded path must keep the DAG shape"
    assert all(n["status"] == "skipped" for n in graph["nodes"])
    assert any(e["to"] == "test" for e in graph["edges"])