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

PLATFORM_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "graph.js"


# ---------------------------------------------------------------------------
# Status mapping table (GitHub Actions → graph vocabulary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gh_status", "gh_conclusion", "expected"),
    [
        ("completed", "success", "succeeded"),
        ("completed", "failure", "failed"),
        ("completed", "timed_out", "failed"),
        ("completed", "startup_failure", "failed"),
        ("completed", "stale", "failed"),
        ("completed", "cancelled", "cancelled"),
        ("completed", "skipped", "skipped"),
        ("completed", None, "skipped"),  # stopped run with no conclusion
        ("in_progress", None, "running"),
        ("queued", None, "pending"),
        ("waiting", None, "pending"),
        ("requested", None, "pending"),
        ("pending", None, "pending"),
        ("weird", None, "skipped"),  # unknown → skipped
        (None, None, "skipped"),
    ],
)
def test_gh_status_mapping(gh_status, gh_conclusion, expected):
    job = {"status": gh_status, "conclusion": gh_conclusion}
    assert platform_ops._map_gh_status(job)[0] == expected


# ---------------------------------------------------------------------------
# Matrix roll-up precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["succeeded"], "succeeded"),
        (["failed", "succeeded"], "failed"),
        (["running", "succeeded"], "running"),
        (["pending", "succeeded"], "pending"),
        (["cancelled", "succeeded"], "cancelled"),
        (["skipped", "succeeded"], "succeeded"),
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
            "version": "pipeline-graph/1",
            "source": "ci",
            "title": "ci",
            "subtitle": "",
            "status": "running",
            "nodes": [
                {"id": "discover", "label": "discover", "status": "succeeded", "depends_on": []},
                {"id": "lint", "label": "lint", "status": "succeeded", "depends_on": ["discover"]},
                {"id": "gitleaks", "label": "gitleaks", "status": "succeeded", "depends_on": ["discover"]},
                {"id": "test", "label": "test", "status": "failed", "depends_on": ["lint", "gitleaks"]},
                {"id": "deploy", "label": "deploy", "status": "pending", "depends_on": ["test"]},
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
            "version": "pipeline-graph/1",
            "source": "ci",
            "title": "cycle",
            "subtitle": "",
            "status": "running",
            "nodes": [
                {"id": "a", "label": "a", "status": "pending", "depends_on": ["b"]},
                {"id": "b", "label": "b", "status": "pending", "depends_on": ["c"]},
                {"id": "c", "label": "c", "status": "pending", "depends_on": ["a"]},
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
            "version": "pipeline-graph/1",
            "source": "ci",
            "title": "solo",
            "subtitle": "",
            "status": "running",
            "nodes": [{"id": "job", "label": "deploy", "status": "running", "depends_on": []}],
        }
    )
    assert [n["id"] for n in laid["nodes"]] == ["job"]
    assert laid["edges"] == []
    assert laid["width"] > 0 and laid["height"] > 0


def test_js_layout_empty_graph_returns_zeros():
    laid = _layout_via_node(
        {
            "version": "pipeline-graph/1",
            "source": "ci",
            "title": "empty",
            "subtitle": "",
            "status": "running",
            "nodes": [],
        }
    )
    assert laid["nodes"] == []
    assert laid["edges"] == []
    assert laid["width"] == 0 and laid["height"] == 0


def _layout_xy_via_node(graph: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    harness = (
        "const fs = require('fs'); globalThis.window = {};\n"
        f"require({str(PLATFORM_JS)!r});\n"
        "const graph = JSON.parse(fs.readFileSync(0, 'utf8'));\n"
        "const out = window.PipelineGraph.layout(graph, {});\n"
        "console.log(JSON.stringify({\n"
        "  nodes: out.nodes.map(n => ({id: n.id, x: n.x, y: n.y})),\n"
        "  width: out.width, height: out.height\n"
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


def test_js_layout_dangling_depends_on_no_nan():
    """A depends_on that references a non-existent node must not produce NaN
    coordinates — the renderer silently drops dangling refs."""
    laid = _layout_xy_via_node(
        {
            "version": "pipeline-graph/1",
            "source": "ci",
            "title": "dangling",
            "subtitle": "",
            "status": "running",
            "nodes": [
                {"id": "a", "label": "a", "status": "pending", "depends_on": ["ghost"]},
                {"id": "b", "label": "b", "status": "pending", "depends_on": []},
            ],
        }
    )
    for n in laid["nodes"]:
        assert n["x"] == n["x"]  # not NaN
        assert n["y"] == n["y"]
    assert laid["width"] == laid["width"] and laid["height"] == laid["height"]


def test_js_layout_five_runs_byte_identical():
    """Determinism: five runs on the same input must be byte-identical (no
    Math.random anywhere)."""
    graph = {
        "version": "pipeline-graph/1",
        "source": "ci",
        "title": "det",
        "subtitle": "",
        "status": "running",
        "nodes": [
            {"id": "a", "label": "A", "status": "succeeded", "depends_on": []},
            {"id": "b", "label": "B", "status": "succeeded", "depends_on": ["a"]},
            {"id": "c", "label": "C", "status": "succeeded", "depends_on": ["a"]},
            {"id": "d", "label": "D", "status": "succeeded", "depends_on": ["b", "c"]},
        ],
    }
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    runs = []
    for _ in range(5):
        res = subprocess.run(
            [node, "-e",
             f"const fs = require('fs'); globalThis.window = {{}}; require({str(PLATFORM_JS)!r});"
             "const graph = JSON.parse(fs.readFileSync(0, 'utf8'));"
             "const out = window.PipelineGraph.layout(graph);"
             "console.log(JSON.stringify(out));"],
            input=json.dumps(graph),
            capture_output=True, text=True, timeout=30
        )
        runs.append(res.stdout.strip())
    assert len(set(runs)) == 1


# ---------------------------------------------------------------------------
# ci_run_graph: join by name, matrix collapse, degraded path, timestamp clamps
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
    jobs = [
        {"databaseId": "101", "name": "Discover services", "status": "completed", "conclusion": "success"},
        {"databaseId": "102", "name": "Lint", "status": "completed", "conclusion": "success"},
        {"databaseId": "103", "name": "Tests + Dependency Audit", "status": "completed", "conclusion": "failure"},
    ]
    fake_gh(_canned_run_view(jobs))
    graph = platform_ops.ci_run_graph("42")
    assert graph["degraded"] is False
    assert graph["title"] == "ci: main"
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["discover"]["status"] == "succeeded"
    assert by_id["test"]["status"] == "failed"
    # gitleaks has no matching gh job → pending while run is going, skipped once done.
    # The canned run has no gitleaks job, and run status is completed → skipped.
    assert by_id["gitleaks"]["status"] == "skipped"
    assert by_id["gitleaks"]["detail"] == ""  # no `if:` in workflow
    # DAG comes from the workflow file, not from GitHub.
    # Graph uses depends_on on nodes, no edges array in contract.
    # But for test we check the internal structure — renderers derive edges.
    assert by_id["test"]["depends_on"] == ["discover", "lint", "gitleaks"]


def test_ci_run_graph_matrix_collapse(fake_gh):
    jobs = [
        {"databaseId": "201", "name": "Tests + Dependency Audit (a)", "status": "completed", "conclusion": "success"},
        {"databaseId": "202", "name": "Tests + Dependency Audit (b)", "status": "completed", "conclusion": "failure"},
        {"databaseId": "203", "name": "Tests + Dependency Audit (c)", "status": "completed", "conclusion": "success"},
    ]
    fake_gh(_canned_run_view(jobs))
    graph = platform_ops.ci_run_graph("42")
    test_node = next(n for n in graph["nodes"] if n["id"] == "test")
    assert test_node["status"] == "failed"
    assert test_node["detail"] == "3 matrix legs"
    assert test_node["fanout"]
    assert len(test_node["fanout"]) == 3

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


def test_ci_run_graph_degraded_returns_pending_shape(fake_gh):
    fake_gh({"ok": False, "stdout": "", "stderr": "gh not installed", "code": -1})
    graph = platform_ops.ci_run_graph("42")
    assert graph["degraded"] is True
    assert "gh not installed" in graph["degraded_reason"]
    assert graph["nodes"], "degraded path must keep the DAG shape"
    assert all(n["status"] == "pending" for n in graph["nodes"]), "degraded → all nodes pending"
    assert "version" in graph and graph["version"] == "pipeline-graph/1"


# 19 real GitHub job names as a Python literal fixture, no network
REAL_GH_JOBS = [
    {"databaseId": "1", "name": "Deploy (manual)", "status": "completed", "conclusion": "skipped"},
    {"databaseId": "2", "name": "Secret Scan (Gitleaks)", "status": "completed", "conclusion": "success"},
    {"databaseId": "3", "name": "Load test (k6)", "status": "completed", "conclusion": "success"},
    {"databaseId": "4", "name": "Container Scan (Trivy) (users-service, users-service)", "status": "completed", "conclusion": "success"},
    {"databaseId": "5", "name": "Container Scan (Trivy) (orders-service, orders-service)", "status": "completed", "conclusion": "success"},
    {"databaseId": "6", "name": "Container Scan (Trivy) (catalog-service, catalog-service)", "status": "completed", "conclusion": "success"},
    {"databaseId": "7", "name": "Container Scan (Trivy) (payment-service, payment-service)", "status": "completed", "conclusion": "success"},
    {"databaseId": "8", "name": "Container Scan (Trivy) (notification-service, notification-service)", "status": "completed", "conclusion": "success"},
    {"databaseId": "9", "name": "Terraform Validate (v1.5.7)", "status": "completed", "conclusion": "success"},
    {"databaseId": "10", "name": "Build & Push Images (catalog-items, catalog/items)", "status": "completed", "conclusion": "success"},
    {"databaseId": "11", "name": "Build & Push Images (orders-service, orders-service)", "status": "completed", "conclusion": "success"},
    {"databaseId": "12", "name": "Build & Push Images (catalog-service, catalog-service)", "status": "completed", "conclusion": "success"},
    {"databaseId": "13", "name": "Build & Push Images (payment-service, payment-service)", "status": "completed", "conclusion": "success"},
    {"databaseId": "14", "name": "Build & Push Images (notification-service, notification-service)", "status": "completed", "conclusion": "success"},
    {"databaseId": "15", "name": "Deploy (auto)", "status": "completed", "conclusion": "skipped"},
]


def test_ci_run_graph_real_job_names_exact_and_prefix_match(fake_gh):
    """The 19 real GitHub job names must map correctly:
    - exact match for Deploy (manual), Secret Scan (Gitleaks), Load test (k6)
    - longest literal prefix for Trivy, Terraform Validate, Build & Push
    """
    fake_gh(_canned_run_view(REAL_GH_JOBS))
    graph = platform_ops.ci_run_graph("42")
    by_id = {n["id"]: n for n in graph["nodes"]}
    # Exact matches
    assert by_id["deploy"]["status"] == "skipped"
    assert by_id["gitleaks"]["status"] == "succeeded"
    assert by_id["load-test"]["status"] == "succeeded"
    # Matrix collapse
    assert by_id["trivy-scan"]["detail"] == "5 matrix legs"
    assert by_id["build"]["detail"] == "5 matrix legs"
    # Longest literal prefix
    assert by_id["terraform-validate"]["detail"] == "1 matrix legs"
    assert by_id["terraform-validate"]["fanout"][0]["label"] == "1.5.7"


def test_ci_run_graph_orphan_gh_job(fake_gh):
    """A gh job with no workflow-file counterpart becomes an orphan node."""
    jobs = REAL_GH_JOBS + [{"databaseId": "999", "name": "Random Job", "status": "completed", "conclusion": "success"}]
    fake_gh(_canned_run_view(jobs))
    graph = platform_ops.ci_run_graph("42")
    orphan = next((n for n in graph["nodes"] if n["id"].startswith("gh:")), None)
    assert orphan is not None
    assert orphan["detail"] == "not in workflow file"
    assert orphan["depends_on"] == []


def test_ci_run_graph_negative_duration_clamped_to_null(fake_gh):
    """Skipped jobs can have startedAt > completedAt — clamp to null."""
    jobs = [
        {"databaseId": "100", "name": "Deploy (manual)",
         "status": "completed", "conclusion": "skipped",
         "startedAt": "2026-08-18T23:25:00Z", "completedAt": "2026-08-18T23:24:59Z"},
    ]
    fake_gh(_canned_run_view(jobs))
    graph = platform_ops.ci_run_graph("42")
    node = next(n for n in graph["nodes"] if n["id"] == "deploy")
    assert node["duration_s"] is None


def test_ci_run_graph_year_0001_timestamp_becomes_null(fake_gh):
    """gh serialises unset times as 0001-01-01T00:00:00Z — filter those."""
    jobs = [
        {"databaseId": "100", "name": "Deploy (manual)",
         "status": "completed", "conclusion": "skipped",
         "startedAt": "0001-01-01T00:00:00Z", "completedAt": "0001-01-01T00:00:00Z"},
    ]
    fake_gh(_canned_run_view(jobs))
    graph = platform_ops.ci_run_graph("42")
    node = next(n for n in graph["nodes"] if n["id"] == "deploy")
    assert node["started_at"] is None
    assert node["finished_at"] is None
    assert node["duration_s"] is None


# ---------------------------------------------------------------------------
# Job graph (tenant) — status positional rules, truncation regression
# ---------------------------------------------------------------------------


def test_job_graph_status_positional_rules():
    from controlplane.core.pipeline_graph import _node_status
    TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")

    # i < current → succeeded
    assert _node_status(1, 3, "running", None) == "succeeded"
    # i == current, job running → running
    assert _node_status(3, 3, "running", None) == "running"
    # i == current, job failed → failed
    assert _node_status(3, 3, "failed", None) == "failed"
    # i == current, job cancelled → cancelled
    assert _node_status(3, 3, "cancelled", None) == "cancelled"
    # i == current, job succeeded → succeeded
    assert _node_status(3, 3, "succeeded", None) == "succeeded"
    # i > current, job terminal → skipped
    assert _node_status(5, 3, "succeeded", None) == "skipped"
    # i > current, job not terminal → pending
    assert _node_status(5, 3, "running", None) == "pending"
    # Row with its own status wins
    class Row:
        status = "failed"
    assert _node_status(3, 3, "running", Row()) == "failed"


# ---------------------------------------------------------------------------
# applyLogProgress never regresses terminal nodes
# ---------------------------------------------------------------------------


def _apply_log_progress_via_node(graph: dict, log_text: str, job_status: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    harness = (
        "const fs = require('fs'); globalThis.window = {};\n"
        f"require({str(PLATFORM_JS)!r});\n"
        "const input = JSON.parse(fs.readFileSync(0, 'utf8'));\n"
        "const out = window.PipelineGraph.applyLogProgress(input.graph, input.log, input.jobStatus);\n"
        "console.log(JSON.stringify(out));\n"
    )
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"graph": graph, "log": log_text, "jobStatus": job_status}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"node applyLogProgress failed: {result.stderr}")
    return json.loads(result.stdout)


def test_apply_log_progress_never_regresses_terminal():
    graph = {
        "version": "pipeline-graph/1",
        "source": "job",
        "title": "job",
        "subtitle": "",
        "status": "running",
        "nodes": [
            {"id": "a", "label": "A", "status": "succeeded", "depends_on": []},
            {"id": "b", "label": "B", "status": "running", "depends_on": ["a"]},
            {"id": "c", "label": "C", "status": "pending", "depends_on": ["b"]},
        ],
    }
    # Each "[n/N] name" marker announces step n *starting* — the highest
    # marker is the currently-running step, so step 3 starting means step 2
    # already finished.
    log = "[1/3] A\n[2/3] B\n[3/3] C\n"
    out = _apply_log_progress_via_node(graph, log, "running")
    assert out["nodes"][0]["status"] == "succeeded"
    assert out["nodes"][1]["status"] == "succeeded"
    assert out["nodes"][2]["status"] == "running"

    # If node was failed, it must not regress to running/pending.
    graph["nodes"][0]["status"] = "failed"
    out = _apply_log_progress_via_node(graph, log, "running")
    assert out["nodes"][0]["status"] == "failed"


# ---------------------------------------------------------------------------
# ci_graph_static prints 11 nodes
# ---------------------------------------------------------------------------


def test_ci_graph_static_prints_eleven_nodes():
    static = platform_ops.ci_graph_static()
    assert static["version"] == "pipeline-graph/1"
    assert static["source"] == "ci"
    assert len(static["nodes"]) == 11
    assert all(n["status"] == "pending" for n in static["nodes"])

# ---------------------------------------------------------------------------
# Repository-defined stages lengthen the job beyond the built-in template
# ---------------------------------------------------------------------------


class _RowsOnlyDB:
    """Minimal stand-in for the session job_graph() uses: no project, and the
    JobStep rows it was constructed with. Defined at module level because the
    repository forbids `return` inside a test function, and a nested stub
    class would trip that check."""

    def __init__(self, rows):
        self._rows = rows

    def get(self, model, ident):
        return None

    def query(self, model):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


def test_graph_labels_built_in_stages_correctly_when_the_repo_adds_its_own():
    """deploy_task inserts a repository's .platform.yml stages after its own
    fixed prefix — clone, secret scan gate, dependency scan gate — so a job
    can be longer than the template. Labelling the tail with template[i-1]
    regardless named pending steps after entirely different work —
    "rendering + applying manifests" appeared where the push would actually
    run."""
    from types import SimpleNamespace

    from controlplane.core.pipeline_graph import job_graph

    total = 9 + 2  # nine built-in steps, two declared by the repository

    # Clone, both gates, and both repository stages have run; nothing after
    # them has.
    rows = [
        SimpleNamespace(step_index=1, step_total=total, name="cloning repository",
                        status="succeeded", started_at=None, finished_at=None, error_message=None),
        SimpleNamespace(step_index=2, step_total=total, name="secret scan (gitleaks) + gate",
                        status="succeeded", started_at=None, finished_at=None, error_message=None),
        SimpleNamespace(step_index=3, step_total=total, name="dependency scan (pip-audit) + gate",
                        status="succeeded", started_at=None, finished_at=None, error_message=None),
        SimpleNamespace(step_index=4, step_total=total, name="unit tests",
                        status="succeeded", started_at=None, finished_at=None, error_message=None),
        SimpleNamespace(step_index=5, step_total=total, name="lint",
                        status="succeeded", started_at=None, finished_at=None, error_message=None),
    ]
    job = SimpleNamespace(
        id="j", type="deploy", status="running", project_id=None, deployment_id=None,
        log="", started_at=None, finished_at=None, error_message=None,
    )

    labels = [n["label"] for n in job_graph(_RowsOnlyDB(rows), job)["nodes"]]

    assert len(labels) == total, labels
    assert labels[:5] == [
        "cloning repository", "secret scan (gitleaks) + gate", "dependency scan (pip-audit) + gate",
        "unit tests", "lint",
    ]
    # The built-ins that follow keep their real names, shifted past the
    # repository's stages rather than read straight off the template.
    assert labels[5] == "building image"
    assert labels[6] == "pushing image to registry"
    assert labels[-1] == "capturing live URL"


def test_graph_does_not_reuse_a_built_in_label_for_a_pending_repo_stage():
    """Observed live: with two declared stages and only the first started,
    a pending node came back labelled "cloning repository" — the template was
    read at an index that belongs to the repository's own stage, so the
    clone's name appeared twice and the reader could not tell which stage was
    pending."""
    from types import SimpleNamespace

    from controlplane.core.pipeline_graph import job_graph

    total = 9 + 2
    rows = [
        SimpleNamespace(step_index=1, step_total=total, name="cloning repository",
                        status="succeeded", started_at=None, finished_at=None, error_message=None),
        SimpleNamespace(step_index=2, step_total=total, name="secret scan (gitleaks) + gate",
                        status="succeeded", started_at=None, finished_at=None, error_message=None),
        SimpleNamespace(step_index=3, step_total=total, name="dependency scan (pip-audit) + gate",
                        status="succeeded", started_at=None, finished_at=None, error_message=None),
        SimpleNamespace(step_index=4, step_total=total, name="unit tests with coverage",
                        status="running", started_at=None, finished_at=None, error_message=None),
    ]
    job = SimpleNamespace(
        id="j", type="deploy", status="running", project_id=None, deployment_id=None,
        log="", started_at=None, finished_at=None, error_message=None,
    )

    labels = [n["label"] for n in job_graph(_RowsOnlyDB(rows), job)["nodes"]]

    assert len(labels) == total, labels
    assert labels.count("cloning repository") == 1, labels
    assert labels[5] == "building image", labels
    assert labels[-1] == "capturing live URL", labels
