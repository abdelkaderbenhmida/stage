import os
import re
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UI_DIR = os.path.join(REPO_ROOT, "ui")
sys.path.insert(0, UI_DIR)

import introspect  # noqa: E402


def test_config_tab_default_is_a_real_tab():
    """Regression guard: state.configTab must be one of renderConfig's tabs,
    or the Operations view renders blank until the user clicks a tab."""
    src = open(os.path.join(UI_DIR, "static", "app.js")).read()
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
    assert ci["triggers"]["push"] == ["branches", "paths"]


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
    assert argocd["generator_type"] == "git"
    assert "app/*" in [f.get("path") for f in argocd["files_pattern"]]
    assert "app/*/*" in [f.get("path") for f in argocd["files_pattern"]]
    assert argocd["sync_policy"]["automated"]["prune"] is True


def test_api_endpoints():
    import importlib.util

    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location(
        "ui_main", os.path.join(UI_DIR, "main.py")
    )
    ui_main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui_main)

    client = TestClient(ui_main.app)
    assert client.get("/api/health").json()["status"] == "ok"
    ov = client.get("/api/overview").json()
    assert ov["service_count"] == len(introspect.discover_services())
    assert ov["status"] == "ready"
    svc = client.get("/api/services").json()
    assert svc["count"] == len(introspect.discover_services())


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


def test_rollout_undo_rejects_invalid_deployment_names():
    for bad in ["../../etc/passwd", "; rm -rf /", "UPPER_CASE", ""]:
        try:
            introspect.rollout_undo("devops-platform", bad)
            assert False, f"expected ServiceError for {bad!r}"
        except introspect.ServiceError:
            pass
