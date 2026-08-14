"""End-to-end API tests against a real PostgreSQL (docs/PLATFORM_SPEC.md §8).

Uses FastAPI TestClient with a testcontainers Postgres and the Celery queue
functions stubbed so nothing escapes to Redis.
"""

import pytest

pytestmark = pytest.mark.integration

VALID_SPEC = {
    "version": 1,
    "project": "my-cluster",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
        {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
    ],
}


@pytest.fixture()
def auth(auth_headers):
    return auth_headers()


def _create_project(client, headers, name="my-cluster", spec=None):
    """POST /projects expects {"name", "infra_spec"} — name is the project's
    registry name, distinct from infra_spec's own "project" field."""
    return client.post(
        "/api/v1/projects",
        json={"name": name, "infra_spec": spec or VALID_SPEC},
        headers=headers,
    )


def test_register_requires_matching_passwords(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "b@example.com", "password": "supersecret123", "password_confirm": "different"},
    )
    assert resp.status_code == 422


def test_register_short_password_rejected(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "b@example.com", "password": "short", "password_confirm": "short"},
    )
    assert resp.status_code == 422


def test_duplicate_email_rejected(client, auth_headers):
    auth_headers(email="alice@example.com")
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "supersecret123", "password_confirm": "supersecret123"},
    )
    assert resp.status_code == 409


def test_login_and_me(client, auth):
    me = client.get("/api/v1/auth/me", headers=auth)
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_login_wrong_password(client):
    resp = client.post(
        "/api/v1/auth/login", json={"email": "alice@example.com", "password": "wrong-password"}
    )
    assert resp.status_code == 401


def test_login_rate_limit(client, monkeypatch):
    from controlplane.api.rate_limit import _limiter

    monkeypatch.setattr("controlplane.api.rate_limit._limiter", type(_limiter)())
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "nope-nope-nope"})
    sixth = client.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "nope-nope-nope"})
    assert sixth.status_code == 429


def test_refresh_token_rotation(client, auth_headers):
    auth_headers()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Str0ng!Passw0rd"},
    )
    assert login.status_code == 200
    refresh = login.json()["refresh_token"]

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    assert resp.json()["access_token"]

    # rotated token must be revoked
    again = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert again.status_code == 401


def test_logout_revokes_refresh(client, auth_headers):
    auth_headers()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Str0ng!Passw0rd"},
    )
    refresh = login.json()["refresh_token"]
    client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401


def test_unauthenticated_requests_rejected(client):
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 401


def test_tampered_token_rejected(client):
    headers = {"Authorization": "Bearer not-a-real-token"}
    resp = client.get("/api/v1/projects", headers=headers)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


def test_create_project(auth_headers, client):
    headers = auth_headers()
    resp = _create_project(client, headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "my-cluster"
    assert body["status"] == "draft"
    assert len(body["nodes"]) == 2


def test_create_project_invalid_spec_rejected(client, auth):
    bad = dict(VALID_SPEC)
    bad["nodes"] = bad["nodes"] + [{"name": "master2", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}]
    resp = _create_project(client, auth, spec=bad)
    assert resp.status_code == 422
    assert "masters" in resp.text or "master" in resp.text


def test_create_project_duplicate_name_rejected(client, auth):
    _create_project(client, auth)
    resp = _create_project(client, auth)
    assert resp.status_code == 409


def test_list_projects_isolated_per_user(client, auth_headers):
    alice = auth_headers(email="alice@example.com")
    bob = auth_headers(email="bob@example.com")
    _create_project(client, alice)
    bob_list = client.get("/api/v1/projects", headers=bob)
    assert bob_list.status_code == 200
    assert bob_list.json() == []


def test_cross_tenant_access_returns_404(client, auth_headers):
    alice = auth_headers(email="alice@example.com")
    bob = auth_headers(email="bob@example.com")
    created = _create_project(client, alice).json()
    pid = created["id"]

    bob_get = client.get(f"/api/v1/projects/{pid}", headers=bob)
    assert bob_get.status_code == 404

    bob_patch = client.patch(f"/api/v1/projects/{pid}", json={"description": "steal"}, headers=bob)
    assert bob_patch.status_code == 404

    bob_delete = client.request(
        "DELETE", f"/api/v1/projects/{pid}", json={"confirm_name": "my-cluster"}, headers=bob
    )
    assert bob_delete.status_code == 404

    bob_nodes = client.get(f"/api/v1/projects/{pid}/nodes", headers=bob)
    assert bob_nodes.status_code == 404

    # alice can still see it
    assert client.get(f"/api/v1/projects/{pid}", headers=alice).status_code == 200


def test_project_cap_enforced(client, auth_headers, monkeypatch):
    headers = auth_headers()
    from controlplane.core.config import settings

    # Settings is a frozen dataclass singleton — monkeypatch.setattr can't
    # write to it directly, and it must be restored manually since it
    # outlives this test.
    original = settings.max_projects_per_user
    object.__setattr__(settings, "max_projects_per_user", 2)
    try:
        for i in range(2):
            spec = dict(VALID_SPEC)
            spec["nodes"] = [dict(spec["nodes"][0], name=f"m{i}"), dict(spec["nodes"][1], name=f"w{i}")]
            assert _create_project(client, headers, name=f"proj-{i}", spec=spec).status_code == 201
        third = dict(VALID_SPEC)
        third["nodes"] = [dict(third["nodes"][0], name="m2"), dict(third["nodes"][1], name="w2")]
        resp = _create_project(client, headers, name="proj-2", spec=third)
        assert resp.status_code == 409
    finally:
        object.__setattr__(settings, "max_projects_per_user", original)


def test_patch_project_updates_spec_and_nodes(client, auth):
    created = _create_project(client, auth).json()
    pid = created["id"]

    patched = dict(VALID_SPEC)
    patched["nodes"] = [dict(patched["nodes"][0], vcpu=8), patched["nodes"][1]]
    resp = client.patch(f"/api/v1/projects/{pid}", json={"infra_spec": patched}, headers=auth)
    assert resp.status_code == 200
    assert resp.json()["nodes"][0]["vcpu"] == 8


def test_provision_queues_job(client, auth, session):
    created = _create_project(client, auth).json()
    pid = created["id"]
    resp = client.post(f"/api/v1/projects/{pid}/provision", headers=auth)
    assert resp.status_code == 202, resp.text
    assert "job_id" in resp.json()
    from controlplane.models import Project

    project = session.get(Project, pid)
    assert project.status == "provisioning"


def test_destroy_requires_name_confirmation(client, auth):
    created = _create_project(client, auth).json()
    pid = created["id"]
    resp = client.post(f"/api/v1/projects/{pid}/destroy", json={"confirm_name": "wrong"}, headers=auth)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


@pytest.fixture()
def ready_project(client, auth):
    """A project whose DB status is flipped to ready so deployments are allowed."""
    created = _create_project(client, auth).json()
    pid = created["id"]
    from controlplane.db import SessionLocal
    from controlplane.models import Project

    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.status = "ready"
        db.commit()
    return pid


def test_deploy_requires_ready_project(client, auth):
    created = _create_project(client, auth).json()
    pid = created["id"]
    resp = client.post(
        f"/api/v1/projects/{pid}/deployments",
        json={"service_name": "users", "repo_url": "https://github.com/org/users.git", "port": 8000},
        headers=auth,
    )
    assert resp.status_code == 409


def test_deploy_rejects_ssh_repo_url(client, auth, ready_project):
    resp = client.post(
        f"/api/v1/projects/{ready_project}/deployments",
        json={"service_name": "users", "repo_url": "git@github.com:org/users.git", "port": 8000},
        headers=auth,
    )
    assert resp.status_code == 422


def test_create_and_list_deployments(client, auth, ready_project):
    resp = client.post(
        f"/api/v1/projects/{ready_project}/deployments",
        json={"service_name": "users", "repo_url": "https://github.com/org/users.git", "port": 8000},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] in ("queued", "draft")

    listing = client.get(f"/api/v1/projects/{ready_project}/deployments", headers=auth)
    assert listing.status_code == 200
    assert len(listing.json()) == 1


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------


def test_create_scan_and_reject_bad_tool(client, auth):
    created = _create_project(client, auth).json()
    pid = created["id"]

    resp = client.post(
        f"/api/v1/projects/{pid}/scans",
        json={"tool": "nuclei", "target": "https://github.com/org/repo.git"},
        headers=auth,
    )
    assert resp.status_code == 422

    resp = client.post(
        f"/api/v1/projects/{pid}/scans",
        json={"tool": "trivy", "target": "https://github.com/org/repo.git"},
        headers=auth,
    )
    assert resp.status_code == 202
    assert len(resp.json()) == 1


def test_scan_all_creates_three(client, auth):
    created = _create_project(client, auth).json()
    pid = created["id"]
    resp = client.post(
        f"/api/v1/projects/{pid}/scans",
        json={"tool": "all", "target": "https://github.com/org/repo.git"},
        headers=auth,
    )
    assert resp.status_code == 202
    assert {scan["tool"] for scan in resp.json()} == {"trivy", "gitleaks", "pip_audit"}


def test_cross_tenant_scan_404(client, auth_headers, session):
    alice = auth_headers(email="alice@example.com")
    bob = auth_headers(email="bob@example.com")
    created = _create_project(client, alice).json()
    pid = created["id"]
    resp = client.post(
        f"/api/v1/projects/{pid}/scans",
        json={"tool": "trivy", "target": "https://github.com/org/repo.git"},
        headers=bob,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_readyz(client):
    assert client.get("/readyz").status_code == 200
