"""A service name identifies one service inside a project.

`create()` inserted unconditionally, so redeploying added another row and one
Kubernetes Deployment ended up described by several. Observed in the console:
the same service listed twice, live and blocked at once, with no way to tell
which row owned the running workload — and deleting either would tear down
the workload the other still claimed.
"""

import pytest
from controlplane.db import SessionLocal
from controlplane.models import Job, Project


SPEC = {
    "version": 1,
    "project": "dupes",
    "mode": "namespace",
    "network": {"cidr": "192.168.56.0/24", "domain": "demo.local"},
    "nodes": [
        {"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
    ],
}


def _project(client, auth, name="dupes"):
    resp = client.post(
        "/api/v1/projects",
        json={"name": name, "infra_spec": {**SPEC, "project": name}},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    # Deployments are refused until the environment exists.
    with SessionLocal() as db:
        db.get(Project, project_id).status = "ready"
        db.commit()
    return project_id


def _deploy(client, auth, project_id, service="web", branch="main", port=8080):
    return client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={
            "service_name": service,
            "repo_url": "https://github.com/org/repo.git",
            "branch": branch,
            "port": port,
        },
        headers=auth,
    )


def _finish_deploy_jobs(deployment_id: str) -> None:
    with SessionLocal() as db:
        for job in db.query(Job).filter(Job.deployment_id == deployment_id).all():
            job.status = "succeeded"
        db.commit()


@pytest.mark.integration
def test_redeploying_a_service_updates_it_instead_of_adding_a_row(auth_headers, client):
    auth = auth_headers("dupes-owner@example.com")
    project_id = _project(client, auth)

    first = _deploy(client, auth, project_id, branch="main", port=8080)
    assert first.status_code == 201, first.text

    # Only one deploy may be in flight per service, so land the first one
    # before redeploying — which is what a user does anyway.
    _finish_deploy_jobs(first.json()["id"])

    second = _deploy(client, auth, project_id, branch="develop", port=9000)
    assert second.status_code == 201, second.text

    listed = client.get(f"/api/v1/projects/{project_id}/deployments", headers=auth).json()
    names = [d["service_name"] for d in listed]
    assert names.count("web") == 1, f"one service, several rows: {names}"

    # The surviving row must describe the *new* request, not the old one.
    only = next(d for d in listed if d["service_name"] == "web")
    assert only["branch"] == "develop"
    assert only["port"] == 9000
    assert only["id"] == first.json()["id"], "the service kept its identity"


@pytest.mark.integration
def test_two_projects_may_each_have_a_service_of_the_same_name(auth_headers, client):
    """Uniqueness is per project, not global — tenants pick their own names."""
    auth = auth_headers("dupes-two@example.com")
    a = _project(client, auth, name="alpha")
    b = _project(client, auth, name="beta")

    assert _deploy(client, auth, a, service="api").status_code == 201
    assert _deploy(client, auth, b, service="api").status_code == 201

    for project_id in (a, b):
        listed = client.get(f"/api/v1/projects/{project_id}/deployments", headers=auth).json()
        assert [d["service_name"] for d in listed] == ["api"]
