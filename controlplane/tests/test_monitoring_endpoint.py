"""Per-project metrics endpoint (multi-tenancy plan §4.1).

Mirrors test_logs_endpoint.py: the point of these tests is that the PromQL
is built server-side around the caller's own namespace and that a foreign or
unknown project is indistinguishable from one that does not exist.
"""

import uuid

import httpx
import pytest

from controlplane.core.validation import k8s_namespace

NS_SPEC = {
    "version": 1,
    "project": "metricsdemo",
    "mode": "namespace",
    "network": {"cidr": "192.168.56.0/24", "domain": "demo.local"},
    "nodes": [
        {"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
    ],
}


def _create_project(client, auth, name="metricsdemo"):
    resp = client.post(
        "/api/v1/projects",
        json={"name": name, "infra_spec": {**NS_SPEC, "project": name}},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _fake_prometheus(monkeypatch, status=200):
    captured = {"queries": []}
    request = httpx.Request("GET", "http://prometheus.monitoring.svc.cluster.local:9090")

    def _get(url, params=None, timeout=None):
        captured["url"] = url
        captured["queries"].append(params["query"])
        if status != 200:
            return httpx.Response(status, json={}, request=request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"result": [{"metric": {}, "values": [["1700000000", "1.5"], ["1700000060", "2.5"]]}]},
            },
            request=request,
        )

    monkeypatch.setattr(httpx, "get", _get)
    return captured


@pytest.mark.integration
def test_metrics_are_scoped_to_the_callers_own_namespace(auth_headers, client, monkeypatch):
    captured = _fake_prometheus(monkeypatch)
    auth = auth_headers()
    project_id = _create_project(client, auth)

    resp = client.get(f"/api/v1/projects/{project_id}/metrics", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    ns = k8s_namespace(uuid.UUID(project_id))
    assert body["namespace"] == ns
    assert body["backend_available"] is True

    # Every query the server issued must be pinned to this project's own
    # namespace — that is the whole isolation guarantee of this endpoint.
    assert captured["queries"], "no queries issued"
    for query in captured["queries"]:
        assert f'namespace="{ns}"' in query
        # Nothing may select a namespace other than this project's own —
        # that is the whole isolation guarantee, so assert it structurally
        # rather than trusting the query strings to stay unchanged.
        import re as _re

        for selected in _re.findall(r'(?:exported_)?namespace="([^"]+)"', query):
            assert selected == ns, f"query escapes the tenant namespace: {query}"

    assert {p["key"] for p in body["panels"]} == {"cpu", "memory", "pods", "restarts"}
    assert body["panels"][0]["latest"] == 2.5


@pytest.mark.integration
def test_metrics_for_a_foreign_project_404(auth_headers, client, monkeypatch):
    _fake_prometheus(monkeypatch)
    owner = auth_headers("owner-metrics@example.com")
    project_id = _create_project(client, owner, name="ownedapp")

    stranger = auth_headers("stranger-metrics@example.com")
    resp = client.get(f"/api/v1/projects/{project_id}/metrics", headers=stranger)
    assert resp.status_code == 404


@pytest.mark.integration
def test_metrics_unknown_project_404(auth_headers, client, monkeypatch):
    _fake_prometheus(monkeypatch)
    resp = client.get(f"/api/v1/projects/{uuid.uuid4()}/metrics", headers=auth_headers())
    assert resp.status_code == 404


@pytest.mark.integration
def test_metrics_unauthenticated_401(client, monkeypatch):
    _fake_prometheus(monkeypatch)
    resp = client.get(f"/api/v1/projects/{uuid.uuid4()}/metrics")
    assert resp.status_code == 401


@pytest.mark.integration
def test_metrics_backend_down_reports_unavailable(auth_headers, client, monkeypatch):
    _fake_prometheus(monkeypatch, status=503)
    auth = auth_headers()
    project_id = _create_project(client, auth)
    resp = client.get(f"/api/v1/projects/{project_id}/metrics", headers=auth)
    # The rest of the project view still works, so this degrades rather than 502s.
    assert resp.status_code == 200
    assert resp.json()["backend_available"] is False


@pytest.mark.integration
def test_metrics_window_is_bounded(auth_headers, client, monkeypatch):
    _fake_prometheus(monkeypatch)
    auth = auth_headers()
    project_id = _create_project(client, auth)
    assert client.get(f"/api/v1/projects/{project_id}/metrics?since_minutes=99999", headers=auth).status_code == 422
    assert client.get(f"/api/v1/projects/{project_id}/metrics?since_minutes=1", headers=auth).status_code == 422
