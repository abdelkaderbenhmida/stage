import os
import re
import sys
from pathlib import Path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

# Importing the console reaches the settings object, which builds an engine
# from DATABASE_URL. Nothing here queries a database, so point it at an
# in-memory one rather than depending on whatever the repo's .env holds.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-for-signing-here")

# The standalone ui/ app was folded into the control plane; its introspection
# module now lives there and is served over /api/v1/platform/*. Same code, so
# these tests keep their original name for it.
from controlplane import platform_ops as introspect

CONSOLE_STATIC = os.path.join(REPO_ROOT, "controlplane", "web", "static")


def console_client():
    """A TestClient over just the platform router, with auth stubbed out.

    Mounted standalone rather than via ``create_app()``: the full console
    pulls in a database and JWT config that these tests neither have nor
    need. Auth and middleware are covered by controlplane/tests; what is
    under test here is the introspection payload.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from controlplane.api.deps import get_current_user
    from controlplane.api.rbac import require_platform_admin
    from controlplane.api.routers import platform

    app = FastAPI()
    app.include_router(platform.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: None
    app.dependency_overrides[require_platform_admin] = lambda: None
    return TestClient(app)


def test_git_failure_looks_like_absence():
    """Regression guard: _git must return "" on failure, not the arg echo —
    git rev-parse of a missing ref prints the argument to stdout AND fails;
    a truthy echo made the pipeline's pr stage report phantom service
    branches ("branch X exists but no open PR") for every service."""
    out = introspect._git(["rev-parse", "--verify", "refs/remotes/origin/__no_such_ref__"])
    assert out == ""


def test_config_tab_default_is_a_real_tab():
    """Regression guard: state.configTab must be one of renderConfig's tabs,
    or the Operations view renders blank until the user clicks a tab."""
    src = Path(os.path.join(CONSOLE_STATIC, "platform", "app.js")).read_text()
    default = re.search(r'configTab:\s*"([^"]+)"', src).group(1)
    tabs = re.search(r'const tabs = \[([^\]]+)\]', src).group(1)
    tab_list = [t.strip().strip('"') for t in tabs.split(",")]
    assert default in tab_list


def test_discovery_finds_only_services_with_main_py():
    services = introspect.discover_services()
    expected = {"users-service", "products-service", "orders-service", "catalog-items"}
    assert expected.issubset(set(services))
    assert "shared" not in services
    assert "catalog" not in services
    by_name: dict[str, str] = {}
    for a in introspect.discover_apps():
        for svc in a["services"]:
            by_name[svc] = a["name"]
    for s in services:
        group = by_name.get(s)
        assert group is not None, f"{s} missing from app groups"
        if group == "default":
            main = os.path.join(REPO_ROOT, "app", s, "main.py")
        else:
            main = os.path.join(REPO_ROOT, "app", group, s[len(group) + 1 :], "main.py")
        assert os.path.isfile(main), f"{main} not found"


def test_apps_grouping():
    apps = introspect.discover_apps()
    names = {a["name"] for a in apps}
    assert "default" in names
    default = next(a for a in apps if a["name"] == "default")
    assert "users-service" in default["services"]
    flat = [s["name"] for s in introspect.services_detail() if s["app"] == "default"]
    assert flat == sorted(flat)


def test_create_delete_service_roundtrip():
    svc = "zz-test-svc"
    target = os.path.join(REPO_ROOT, "app", svc)
    try:
        introspect.create_service("", svc)
        assert os.path.isfile(os.path.join(target, "main.py"))
        assert os.path.isfile(os.path.join(target, "requirements.txt"))
        assert svc in introspect.discover_services()
        meta = next(s for s in introspect.services_detail() if s["name"] == svc)
        assert meta["endpoints"] == ["/", "/livez", "/metrics", "/readyz"]
        assert meta["uses_vault"] is True
    finally:
        introspect.delete_service("", svc)
    assert svc not in introspect.discover_services()
    assert not os.path.exists(target)


def test_create_delete_app_with_nested_service():
    app_name, svc_name, k8s = "zz-test-app", "cart", "zz-test-app-cart"
    app_dir = os.path.join(REPO_ROOT, "app", app_name)
    try:
        introspect.create_app(app_name)
        introspect.create_service(app_name, svc_name)
        assert os.path.isfile(os.path.join(app_dir, svc_name, "main.py"))
        assert k8s in introspect.discover_services()
        apps = introspect.discover_apps()
        cart_app = next(a for a in apps if a["name"] == app_name)
        assert cart_app["services"] == [k8s]
        assert introspect.helm_render(introspect.discover_services())["ok"] is True
    finally:
        if os.path.isdir(app_dir):
            introspect.delete_app(app_name)
    assert k8s not in introspect.discover_services()


def test_validation_errors():
    import pytest

    with pytest.raises(introspect.ServiceError):
        introspect.create_app("BAD_NAME")
    with pytest.raises(introspect.ServiceError):
        introspect.create_service("", "bad name")
    with pytest.raises(introspect.ServiceError):
        introspect.delete_service("", "does-not-exist")


def test_helm_renders_nx5_plus_3():
    services = introspect.discover_services()
    result = introspect.helm_render(services)
    assert result["ok"] is True
    assert result["total"] == len(services) * 5 + 3
    assert result["counts"]["Deployment"] == len(services)
    assert result["counts"]["HorizontalPodAutoscaler"] == len(services)
    assert result["counts"]["PodDisruptionBudget"] == len(services)
    assert result["counts"]["ServiceMonitor"] == 1
    shared_kinds = {o["kind"] for o in result["shared"]}
    assert {"Role", "RoleBinding", "ServiceMonitor"}.issubset(shared_kinds)


def test_ci_discovery_is_source_of_truth():
    ci = introspect.parse_ci()
    assert ci["discovery"]["id"] == "discover"
    assert "build" in ci["uses_fromjson"]
    assert "trivy-scan" in ci["uses_fromjson"]
    assert ci["triggers"]["push"] == ["branches", "paths-ignore"]


def test_vault_provisioning_loop_is_discovery_driven():
    vault = introspect.parse_vault()
    lp = vault["per_service"]
    assert lp["present"] is True
    assert lp["policy_template"] == "devops-platform-${svc}"
    assert lp["bound_sa_template"] == "${svc}-sa"
    assert vault["services_source"] == "SERVICES"


def test_monitoring_matches_any_service_count():
    mon = introspect.parse_monitoring()
    assert mon["service_monitor"]["match_labels"] == {
        "app.kubernetes.io/part-of": "devops-platform"
    }
    assert mon["slo_detail"]["availability"]["matcher"] == (
        'up{part_of="devops-platform"}'
    )
    assert any(r["name"] == "SLOAvailabilityBreach" for r in mon["rules"])


def test_argocd_generates_one_app_per_service():
    argocd = introspect.parse_argocd()
    assert argocd["generator_type"] == "files"
    # Exactly ONE generator, matching both the flat (app/<svc>/service.yaml) and
    # grouped (app/<app>/<svc>/service.yaml) layouts via doublestar.
    #
    # It must not be split into the two patterns this test previously required:
    # ArgoCD's matcher lets `*` cross `/`, so `app/*/service.yaml` also matches
    # the nested marker and every grouped service is generated twice, which
    # aborts the whole set with "contains applications with duplicate name".
    assert argocd["files_pattern"] == ["app/**/service.yaml"]
    assert argocd["helm_tag_param"] is True
    assert argocd["sync_policy"]["automated"]["prune"] is True


def test_service_markers_exist_for_all_discovered_services():
    """C1: every service gets a marker file (name + tag). The tag must
    default to 'secondary' — CI rewrites it to commit-<sha7> after builds."""
    markers = introspect._list_service_markers()
    names = {m["name"] for m in markers}
    assert names == set(introspect.discover_services())
    for m in markers:
        assert m["tag"], f"{m['path']} has empty tag"
    assert {"users-service", "products-service", "orders-service", "catalog-items"}.issubset(names)


def test_shared_app_syncs_all_services():
    """C4: the shared RoleBinding owner app must list every discovered
    service — when a new service is created the Application is regenerated."""
    import yaml

    docs = list(yaml.safe_load_all(introspect.SHARED_APP_PATH.read_text()))
    target = next(
        d for d in docs
        if isinstance(d, dict) and d.get("kind") == "Application"
        and (d.get("metadata") or {}).get("name") == "devops-platform-shared"
    )
    params = {
        p["name"]: p["value"]
        for p in target["spec"]["source"]["helm"]["parameters"]
    }
    assert params["renderShared"] == "true"
    assert params["renderSharedOnly"] == "true"
    svc_params = sorted(v for k, v in params.items() if k.startswith("services["))
    assert svc_params == sorted(introspect.discover_services())


def test_ci_tag_backfill_writes_commit_tag_into_markers():
    """C3: after a successful push build, a job pins each marker's tag to the
    commit SHA — must exist in the workflow and be gated on push."""
    wf = Path(os.path.join(REPO_ROOT, ".github/workflows/ci-cd.yml")).read_text()
    assert "tag-backfill" in wf or "Write build tag" in wf
    assert "GITHUB_SHA" in wf
    assert "service.yaml" in wf
    assert "paths-ignore" in wf
    # tag-backfill depends on discover (for the path-filtered changed_services
    # list) as well as build — needs: [discover, build], not needs: build.
    assert "needs: [discover, build]" in wf


def test_no_partial_manifests_outside_patch_files():
    """Drift guard: k8s/apps/*.yaml must contain only complete, single-doc
    manifests (chart owns per-service manifests; patch files are the only
    partial artifacts allowed)."""
    import yaml

    root = os.path.join(REPO_ROOT, "k8s/apps")
    for name in sorted(os.listdir(root)):
        if not name.endswith(".yaml") and not name.endswith(".yml"):
            continue
        if name.endswith("-patch.yaml"):
            continue
        path = os.path.join(root, name)
        with open(path) as f:
            docs = [d for d in yaml.safe_load_all(f) if d]
        assert len(docs) == 1, f"{path}: {len(docs)} docs — split or move to a -patch.yaml"


def test_service_pipeline_stage_transitions():
    """WS-A: the tracker must run through its stages in order and stop at the
    first failure — a service with nothing in git names 'committed' as its
    blocker."""
    r = introspect._pipeline_result(
        "zz-svc",
        [
            {"stage": "files", "state": "ok", "detail": "present"},
            {"stage": "committed", "state": "failed", "detail": "never pushed"},
        ],
    )
    assert r["all_ok"] is False
    assert r["blocking"] == "committed"
    assert r["blocking_detail"] == "never pushed"
    r2 = introspect._pipeline_result(
        "zz-svc",
        [
            {"stage": "files", "state": "ok", "detail": "present"},
            {"stage": "committed", "state": "ok", "detail": "pushed"},
            {"stage": "ci", "state": "pending", "detail": "build running"},
        ],
    )
    assert r2["all_ok"] is False
    assert r2["blocking"] == "ci"


def test_terraform_import_generation():
    """WS-B: import IDs must be the node name (libvirt domain id) or the
    full pool volume key (libvirt stores volume ids as /path/key, not the
    bare name) — the known bug put the node name on the wrong side."""
    assert introspect._import_id("libvirt_domain", "workers", "worker-02") == "worker-02"
    vol_id = introspect._import_id("libvirt_volume", "workers", "worker-02")
    assert vol_id == "/var/lib/libvirt/images/worker-02.qcow2", vol_id
    needed = introspect._terraform_imports_needed()
    assert all("cloudinit_disk" not in a for a, _ in needed), "cloudinit_disk has no provider import support"
    for addr, id_value in needed:
        assert ":" not in addr.split(".")[-1].split("[")[0]
        assert id_value, f"{addr} has empty import id"
        assert not addr.endswith(":"), f"node name leaked after colon: {addr}"
        assert not id_value.startswith(":"), f"empty address before colon: {id_value}"


def test_tfvars_conformance():
    """WS-B: terraform.tfvars must only declare variables that exist in
    variables.tf, and must contain libvirt values (not stale AWS ones)."""
    declared = introspect._declared_tf_vars()
    assert "ssh_user" in declared
    assert "worker_count" in declared
    assert "disk_size_gb" in declared
    keys = introspect._tfvars_keys()
    for k in keys:
        assert k in declared, f"undeclared var in tfvars: {k}"


def test_preflight_refuses_without_space():
    """WS-B: node-add must be refused when the host lacks enough free disk.

    The request is sized past any real disk rather than relying on how full
    the machine happens to be — this test used to assert the free space of
    the laptop it was written on ("1.8G free < 20G") and started failing the
    moment it ran anywhere roomier, without node_preflight changing at all.
    """
    huge_disk_gb = 10 ** 9  # ~1 EB: no host satisfies this.
    r = introspect.node_preflight(huge_disk_gb, 4096)
    assert r["ok"] is False
    assert any("disk" in p.lower() for p in r["problems"])


def test_preflight_accepts_a_request_the_host_can_satisfy():
    """The mirror of the refusal: a trivially small request must not be
    rejected *for disk reasons*, so the check is discriminating rather than
    always-refusing. Other problems (terraform state, memory) may still be
    reported, so only the disk complaint is asserted against."""
    r = introspect.node_preflight(1, 1)
    assert not any("free disk" in p.lower() for p in r["problems"])


def test_command_allowlist_blocks_path_traversal():
    """Security guard: client-supplied commands are allowlisted — nothing
    but the declared script keys may run."""
    for bad in ["../../etc/passwd", "/bin/sh", "worker-add --force", ""]:
        try:
            introspect.run_script(bad)
            assert False, f"expected ServiceError for {bad!r}"
        except introspect.ServiceError:
            pass


def test_api_endpoints(monkeypatch):
    def fake_pipeline(service):
        return {
            "service": service,
            "stages": [],
            "blocking": None,
            "all_ok": True,
            "blocking_detail": None,
        }

    monkeypatch.setattr(introspect, "service_pipeline", fake_pipeline)

    client = console_client()
    assert client.get("/api/v1/platform/health").json()["status"] == "ok"
    ov = client.get("/api/v1/platform/overview").json()
    assert ov["service_count"] == len(introspect.discover_services())
    assert ov["status"] == "healthy"
    svc = client.get("/api/v1/platform/services").json()
    assert svc["count"] == len(introspect.discover_services())


def test_overview_status_is_degraded_when_a_service_is_stuck(monkeypatch):
    """WS-D: honest status — one stuck service makes the platform degraded,
    and the blocker names the first failing stage."""
    def fake_pipeline(service):
        return {
            "service": service,
            "stages": [],
            "blocking": "vault" if service == "catalog-items" else None,
            "all_ok": service != "catalog-items",
            "blocking_detail": "secrets missing" if service == "catalog-items" else None,
        }

    monkeypatch.setattr(introspect, "service_pipeline", fake_pipeline)

    ov = console_client().get("/api/v1/platform/overview").json()
    assert ov["status"] == "degraded"
    blockers = {b["service"]: b["stage"] for b in ov["blockers"]}
    assert blockers["catalog-items"] == "vault"


def test_parse_top_output():
    text = (
        "orders-service-7fcd59d998-qv798    3m    43Mi\n"
        "products-service-96d467b44-hnfhl   3m    44Mi\n"
    )
    metrics = introspect._parse_top_output(text)
    assert metrics["orders-service-7fcd59d998-qv798"] == {"cpu": "3m", "memory": "43Mi"}
    assert len(metrics) == 2


def test_parse_top_output_ignores_malformed_lines():
    assert introspect._parse_top_output("\n  \ntoo-few-fields\n") == {}


def test_parse_rollout_history():
    text = (
        "deployment.apps/users-service\n"
        "REVISION  CHANGE-CAUSE\n"
        "1         <none>\n"
        "2         initial deploy\n"
    )
    revisions = introspect._parse_rollout_history(text)
    assert revisions == [
        {"revision": 1, "change_cause": "<none>"},
        {"revision": 2, "change_cause": "initial deploy"},
    ]


def test_alertmanager_receiver_is_wired():
    """Regression guard: this was silently dropping every alert before this
    session — receivers must not be an empty webhook list, and the URL
    must point at the alert-sink Service actually deployed alongside it."""
    import yaml

    cfg_path = os.path.join(REPO_ROOT, "k8s/monitoring/alertmanager/alertmanager-config.yaml")
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    receivers = cfg["spec"]["receivers"]
    default = next(r for r in receivers if r["name"] == "default")
    assert default["webhookConfigs"], "receiver has no webhook — alerts are silently dropped"
    url = default["webhookConfigs"][0]["url"]
    assert "alert-sink" in url

    sink_path = os.path.join(REPO_ROOT, "k8s/monitoring/alertmanager/alert-sink.yaml")
    with open(sink_path) as f:
        sink_docs = list(yaml.safe_load_all(f))
    svc_names = {d["metadata"]["name"] for d in sink_docs if d.get("kind") == "Service"}
    assert "alert-sink" in svc_names


def test_alertmanager_cr_loads_the_config():
    """Regression guard for the actual bug: an AlertmanagerConfig with a
    real webhook does nothing if the Alertmanager CR never selects it."""
    import yaml

    am_path = os.path.join(REPO_ROOT, "k8s/monitoring/alertmanager/alertmanager.yaml")
    with open(am_path) as f:
        docs = [d for d in yaml.safe_load_all(f) if d and d.get("kind") == "Alertmanager"]
    spec = docs[0]["spec"]
    assert "alertmanagerConfiguration" in spec or "alertmanagerConfigSelector" in spec


def test_script_allowlist_rejects_unknown_keys():
    for bad in ["../../etc/passwd", "/bin/sh", "smoke-test.sh", ""]:
        try:
            introspect.run_script(bad)
            assert False, f"expected ServiceError for {bad!r}"
        except introspect.ServiceError:
            pass


def test_script_allowlist_paths_all_exist_and_are_executable():
    for key, cfg in introspect.SCRIPTS.items():
        path = introspect.ROOT / cfg["path"]
        assert path.is_file(), f"{key}: {cfg['path']} does not exist"
        assert os.access(path, os.X_OK), f"{key}: {cfg['path']} is not executable"


def test_repo_declares_the_whole_logging_stack():
    """Loki and promtail must be in git, not only in the cluster.

    This test used to assert the opposite — that they were absent — because
    they had been running for weeks with no manifests anywhere, and it pinned
    that drift as the expected state. When the cluster was next rebuilt they
    simply did not come back, and every tenant's Logs panel reported "Log
    backend unavailable" with nothing explaining why. The manifests now exist,
    so the assertion is that they stay.
    """
    decl = introspect.repo_declared_objects()
    assert ("Deployment", "kibana") in decl
    assert ("Deployment", "loki") in decl, "Loki is deployed but undeclared — it will not survive a rebuild"
    assert ("DaemonSet", "promtail") in decl, "promtail is deployed but undeclared"


def test_rollout_undo_rejects_invalid_deployment_names():
    for bad in ["../../etc/passwd", "; rm -rf /", "UPPER_CASE", ""]:
        try:
            introspect.rollout_undo("devops-platform", bad)
            assert False, f"expected ServiceError for {bad!r}"
        except introspect.ServiceError:
            pass


def test_graph_js_script_tag_present_and_first():
    """graph.js must be loaded before platform/app.js and app.js, since it
    defines window.PipelineGraph that both consumers call."""
    src = Path(os.path.join(CONSOLE_STATIC, "index.html")).read_text()
    scripts = re.findall(r'<script src="([^"]+)"></script>', src)
    assert "/static/graph.js" in scripts
    assert scripts.index("/static/graph.js") < scripts.index("/static/platform/app.js")
    assert scripts.index("/static/graph.js") < scripts.index("/static/app.js")


def test_graph_js_assigns_exactly_one_global():
    src = Path(os.path.join(CONSOLE_STATIC, "graph.js")).read_text()
    assigns = re.findall(r"window\.PipelineGraph\s*=", src)
    assert len(assigns) == 1
    assert "window.PipelineGraph" in src
    # No stray globals leaking: every bare window.X assignment is the one export.
    others = re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", src)
    assert set(others) == {"PipelineGraph"}


def test_every_pg_class_used_in_graph_js_has_a_rule_in_shell_css():
    js = Path(os.path.join(CONSOLE_STATIC, "graph.js")).read_text()
    css = Path(os.path.join(CONSOLE_STATIC, "shell.css")).read_text()
    js_classes = set(re.findall(r"pg-[a-zA-Z0-9-]+", js))
    # ID prefixes "pg-t-" / "pg-d-" are aria id targets, not classes.
    js_classes -= {"pg-t-", "pg-d-"}
    css_classes = set(re.findall(r"\.(pg-[a-zA-Z0-9-]+)", css))
    missing = js_classes - css_classes
    assert not missing, f"classes used in graph.js without a rule in shell.css: {sorted(missing)}"


def test_pg_classes_do_not_leak_into_scoped_stylesheets():
    for path in ["style.css", os.path.join("platform", "style.css")]:
        css = Path(os.path.join(CONSOLE_STATIC, path)).read_text()
        assert ".pg-" not in css, f"{path} must not define .pg-* classes"


def test_graph_data_acts_exist_in_actions():
    """Every data-act emitted by the graph/CI rendering code must be a real
    ACTIONS key, or clicks die silently on the Operations view."""
    src = Path(os.path.join(CONSOLE_STATIC, "platform", "app.js")).read_text()
    actions_block = src[src.index("const ACTIONS = {"):]
    acts = {"showCiGraph", "refreshConfigTab", "ciTrigger"}
    missing = [a for a in acts if re.search(rf"^\s*{a}:", actions_block, re.MULTILINE) is None]
    assert not missing, f"data-act used by graph tab not wired in ACTIONS: {missing}"
