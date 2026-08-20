"""The per-project CI view answers about the TENANT's repositories, and
fans its `gh` calls out rather than adding their timeouts together.

Two things worth pinning:

1. The repository must come from the deployment's own `repo_url`. Resolving
   it through `platform_ops._repo_slug()` — which reads this control plane's
   own git remote — is what made every tenant see the platform's pipeline
   instead of their app's.

2. Each repository costs one `gh` subprocess with its own 25s timeout. Run
   in sequence, a project with a handful of repositories adds those timeouts
   together and holds a worker for minutes whenever GitHub is slow. The
   endpoint must cost about as much as its slowest repository, not the sum.
"""

from __future__ import annotations

import time
import uuid

import pytest
from controlplane import platform_ops

pytestmark = pytest.mark.integration

NS_SPEC = {
    "version": 1,
    "project": "ci-view",
    "mode": "namespace",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [{"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}],
}

_SLOW_CALL_S = 0.6


@pytest.fixture()
def project_with_repos(client, auth_headers):
    """A project deploying three DISTINCT repositories."""
    auth = auth_headers(email=f"ci-{uuid.uuid4().hex[:8]}@example.com")
    resp = client.post(
        "/api/v1/projects",
        json={"name": f"ci-{uuid.uuid4().hex[:6]}", "infra_spec": NS_SPEC},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]

    # Deployments are refused until the project is provisioned; this test is
    # about the CI view, not the provisioning path, so mark it ready directly.
    from controlplane.db import SessionLocal
    from controlplane.models import Project

    with SessionLocal() as db:
        db.get(Project, uuid.UUID(project_id)).status = "ready"
        db.commit()

    for index, repo in enumerate(
        ["https://github.com/acme/alpha.git",
         "https://github.com/acme/beta.git",
         "https://github.com/acme/gamma.git"],
        start=1,
    ):
        created = client.post(
            f"/api/v1/projects/{project_id}/deployments",
            json={"service_name": f"svc-{index}", "repo_url": repo, "branch": "main", "port": 8000},
            headers=auth,
        )
        assert created.status_code == 201, created.text
    return project_id, auth


def test_ci_reports_the_tenants_own_repositories(client, project_with_repos, monkeypatch):
    project_id, auth = project_with_repos

    def _fake(slug, limit):
        return {"reachable": True, "repo": slug, "runs": []}

    # If the endpoint ever reaches for the platform's own remote instead of
    # the deployment's repo_url, this blows up rather than silently showing
    # the wrong pipeline.
    monkeypatch.setattr(platform_ops, "ci_runs_for_repo", _fake)
    monkeypatch.setattr(
        platform_ops, "_repo_slug",
        lambda: pytest.fail("resolved the platform's own repo, not the tenant's"),
    )

    body = client.get(f"/api/v1/projects/{project_id}/ci", headers=auth).json()

    assert {r["repo"] for r in body["repos"]} == {"acme/alpha", "acme/beta", "acme/gamma"}


def test_ci_calls_are_concurrent_not_serial(client, project_with_repos, monkeypatch):
    """Three slow repositories must not cost three times one repository."""
    project_id, auth = project_with_repos

    def _slow(slug, limit):
        time.sleep(_SLOW_CALL_S)
        return {"reachable": True, "repo": slug, "runs": []}

    monkeypatch.setattr(platform_ops, "ci_runs_for_repo", _slow)

    started = time.monotonic()
    resp = client.get(f"/api/v1/projects/{project_id}/ci", headers=auth)
    elapsed = time.monotonic() - started

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["repos"]) == 3
    # Serial would be >= 3 * _SLOW_CALL_S; concurrent lands near one call.
    # The bound is deliberately loose so this measures the shape, not the
    # machine.
    assert elapsed < _SLOW_CALL_S * 2.5, f"looks serial: {elapsed:.2f}s for 3 x {_SLOW_CALL_S}s"


def test_one_unreadable_repository_does_not_break_the_others(client, project_with_repos, monkeypatch):
    """Fail soft per repository: a repo whose runs cannot be read reports its
    own error instead of blanking the whole view."""
    project_id, auth = project_with_repos

    def _mixed(slug, limit):
        if slug == "acme/beta":
            return {"reachable": False, "error": "gh CLI unavailable", "repo": slug, "runs": []}
        return {"reachable": True, "repo": slug, "runs": []}

    monkeypatch.setattr(platform_ops, "ci_runs_for_repo", _mixed)

    by_repo = {r["repo"]: r for r in client.get(
        f"/api/v1/projects/{project_id}/ci", headers=auth).json()["repos"]}

    assert by_repo["acme/beta"]["reachable"] is False
    assert by_repo["acme/alpha"]["reachable"] is True
    assert by_repo["acme/gamma"]["reachable"] is True


def test_ci_of_another_tenants_project_is_not_found(client, project_with_repos, auth_headers):
    project_id, _ = project_with_repos
    stranger = auth_headers(email=f"stranger-{uuid.uuid4().hex[:6]}@example.com")

    assert client.get(f"/api/v1/projects/{project_id}/ci", headers=stranger).status_code == 404


def test_ci_requires_authentication(client, project_with_repos):
    project_id, _ = project_with_repos

    assert client.get(f"/api/v1/projects/{project_id}/ci").status_code == 401
