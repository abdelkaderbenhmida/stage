"""Per-project Loki logs endpoint (docs/TODO.md §4.2 step 4).

The endpoint must never forward raw client query text to Loki: the LogQL
is assembled server-side around the caller's project name, so a caller
cannot escape their own namespace. Loki itself is mocked via
httpx.MockTransport.
"""

import uuid

import httpx
import pytest
from controlplane.api.routers.logs import _escape_logql_string, build_query
from controlplane.core.config import settings
from controlplane.core.validation import k8s_namespace

NS_SPEC = {
    "version": 1,
    "project": "demo",
    "mode": "namespace",
    "network": {"cidr": "192.168.56.0/24", "domain": "demo.local"},
    "nodes": [
        {"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
    ],
}


def _create_project(client, auth):
    resp = client.post(
        "/api/v1/projects",
        json={"name": "demo", "infra_spec": NS_SPEC},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_build_query_scopes_to_namespace():
    assert build_query("demo", "") == '{namespace="demo"}'
    assert build_query("demo", "panic") == '{namespace="demo"} |= "panic"'


def test_build_query_escapes_search_quotes():
    query = build_query("demo", 'a"b\\c')
    assert query == '{namespace="demo"} |= "a\\"b\\\\c"'


def _fake_loki(monkeypatch, payload=None, status=200):
    captured = {}
    request = httpx.Request("GET", "http://loki.monitoring.svc.cluster.local:3100")

    def _get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        if status != 200:
            return httpx.Response(status, json={}, request=request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "stream": {"namespace": "demo", "pod": "demo-0"},
                            "values": [
                                ["1700000000000000000", "line one"],
                                ["1700000005000000000", "line two"],
                            ],
                        }
                    ]
                },
            }
            if payload is None
            else payload,
            request=request,
        )

    monkeypatch.setattr(httpx, "get", _get)
    return captured


@pytest.mark.integration
def test_logs_endpoint_returns_lines(auth_headers, client, monkeypatch):
    captured = _fake_loki(monkeypatch)
    auth = auth_headers()
    project_id = _create_project(client, auth)
    resp = client.get("/api/v1/logs?project=demo&search=panic", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project"] == "demo"
    # The namespace is derived from the project's UUID, not its name — Phase
    # 2 of the multi-tenancy plan (two teams can each name a project "demo").
    ns = k8s_namespace(uuid.UUID(project_id))
    assert body["query"] == f'{{namespace="{ns}"}} |= "panic"'
    assert body["lines"][0]["line"] == "line one"
    assert body["lines"][1]["line"] == "line two"
    assert "loki/api/v1/query_range" in captured["url"]
    assert captured["params"]["limit"] == 200


@pytest.mark.integration
def test_logs_unknown_project_404s(auth_headers, client, monkeypatch):
    _fake_loki(monkeypatch)
    resp = client.get("/api/v1/logs?project=nope-xyz", headers=auth_headers())
    assert resp.status_code == 404


@pytest.mark.integration
def test_logs_rejects_invalid_project_name(auth_headers, client, monkeypatch):
    _fake_loki(monkeypatch)
    resp = client.get("/api/v1/logs?project=UPPER_CASE", headers=auth_headers())
    assert resp.status_code == 422


@pytest.mark.integration
def test_logs_backend_down_returns_502(auth_headers, client, monkeypatch):
    _fake_loki(monkeypatch, status=503)
    auth = auth_headers()
    _create_project(client, auth)
    resp = client.get("/api/v1/logs?project=demo", headers=auth)
    assert resp.status_code == 502


@pytest.mark.integration
def test_logs_unauthenticated_401(client, monkeypatch):
    _fake_loki(monkeypatch)
    resp = client.get("/api/v1/logs?project=demo")
    assert resp.status_code == 401


@pytest.mark.integration
def test_logs_limit_capped(auth_headers, client, monkeypatch):
    captured = _fake_loki(monkeypatch)
    auth = auth_headers()
    _create_project(client, auth)
    resp = client.get("/api/v1/logs?project=demo&limit=5000", headers=auth)
    assert resp.status_code == 422
    resp = client.get("/api/v1/logs?project=demo&limit=0", headers=auth)
    assert resp.status_code == 422
    resp = client.get("/api/v1/logs?project=demo&limit=500", headers=auth)
    assert resp.status_code == 200
    assert captured["params"]["limit"] == 500


def test_loki_url_falls_back_to_the_in_cluster_service(monkeypatch):
    """With nothing configured, Loki is addressed by its Service name.

    Asserted against a freshly built Settings with LOKI_URL removed, rather
    than against the ambient value: a local install has to point this at a
    port-forward, and the previous version of this test failed the moment
    anyone did.
    """
    from controlplane.core.config import Settings

    monkeypatch.delenv("LOKI_URL", raising=False)
    assert Settings().loki_url.endswith("loki.monitoring.svc.cluster.local:3100")


def test_loki_url_is_taken_from_the_environment_when_set(monkeypatch):
    from controlplane.core.config import Settings

    monkeypatch.setenv("LOKI_URL", "http://127.0.0.1:3100")
    assert Settings().loki_url == "http://127.0.0.1:3100"


def test_escape_helpers():
    assert _escape_logql_string('a"b') == 'a\\"b'
    assert _escape_logql_string("a\\b") == "a\\\\b"
