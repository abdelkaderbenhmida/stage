"""Every /platform route must 404 for a non-admin (multi-tenancy plan Phase 0).

Enumerates the router's own route table rather than hand-listing endpoints,
so a future route added to platform.py without thinking about auth is caught
here automatically instead of silently inheriting whatever the dependency
default happens to be.
"""

import re
import uuid

import pytest

from controlplane.api.routers.platform import router as platform_router

_PATH_PARAM_VALUES = {
    "app_name": "demo",
    "service": "demo",
    "namespace": "devops-platform",
    "pod": "demo-0",
    "deployment": "demo",
    "run_id": "1",
}


def _fill_path(path: str) -> str:
    def _sub(match: re.Match) -> str:
        name = match.group(1)
        return _PATH_PARAM_VALUES.get(name, "x")

    return re.sub(r"\{([^}]+)\}", _sub, path)


def _routes():
    for route in platform_router.routes:
        for method in route.methods - {"HEAD", "OPTIONS"}:
            yield method, _fill_path(route.path)


@pytest.mark.integration
@pytest.mark.parametrize("method,path", list(_routes()))
def test_non_admin_gets_404(client, auth_headers, method, path):
    headers = auth_headers()
    full_path = f"/api/v1/platform{path}"
    resp = client.request(method, full_path, headers=headers, json={})
    assert resp.status_code == 404, f"{method} {full_path} returned {resp.status_code}, expected 404"


def test_router_has_routes():
    """A pure sanity check that the enumeration above isn't silently empty —
    if it were, every parametrized case above would vacuously pass."""
    assert len(list(_routes())) >= 30


@pytest.mark.integration
def test_platform_admin_not_blocked(client, session, monkeypatch):
    from controlplane.core.security import hash_password
    from controlplane.models import User

    admin = User(email=f"admin-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("Sup3rSecret!"), role="admin")
    session.add(admin)
    session.commit()

    login = client.post(
        "/api/v1/auth/login",
        json={"email": admin.email, "password": "Sup3rSecret!"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/v1/platform/health", headers=headers)
    assert resp.status_code != 404
