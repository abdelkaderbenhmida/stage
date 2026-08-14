"""Service catalogue integration tests (docs/TODO.md Task 5.3).

The catalogue aggregates deployments across the caller's teams with the
latest security posture and a straight link to the latest job logs.
"""

import pytest
from controlplane.db import SessionLocal
from controlplane.models import Deployment, Finding, Job, Project, Scan

pytestmark = [pytest.mark.integration]

VM_SPEC = {
    "version": 1,
    "project": "cat-project",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
    ],
}


def _ready_project(client, auth, name="cat-project") -> str:
    resp = client.post("/api/v1/projects", json={"name": name, "infra_spec": VM_SPEC}, headers=auth)
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]
    with SessionLocal() as db:
        db.get(Project, pid).status = "ready"
        db.commit()
    return pid


def _deploy(client, auth, pid, service="users") -> str:
    resp = client.post(
        f"/api/v1/projects/{pid}/deployments",
        json={"service_name": service, "repo_url": f"https://github.com/org/{service}.git", "port": 8000},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _seed_job_and_findings(pid: str, dep_id: str, criticals: int = 0, highs: int = 0) -> None:
    with SessionLocal() as db:
        job = Job(project_id=pid, deployment_id=dep_id, type="deploy", status="succeeded", log="all good")
        db.add(job)
        db.flush()
        scan = Scan(project_id=pid, status="completed", tool="trivy", target="repo:users")
        db.add(scan)
        db.flush()
        for _ in range(criticals):
            db.add(Finding(scan_id=scan.id, severity="critical", title="cri"))
        for _ in range(highs):
            db.add(Finding(scan_id=scan.id, severity="high", title="hi"))
        db.commit()
        return str(job.id)


def test_catalogue_lists_services_with_links_and_filters(client, auth_headers, session):
    auth_headers = auth_headers()
    pid = _ready_project(client, auth_headers)
    dep_id = _deploy(client, auth_headers, pid, "users")
    _seed_job_and_findings(pid, dep_id, criticals=2, highs=1)

    resp = client.get("/api/v1/catalogue", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    entries = resp.json()
    assert len(entries) == 1
    e = entries[0]
    assert e["service_name"] == "users"
    assert e["project_name"] == "cat-project"
    assert e["critical"] == 2 and e["high"] == 1
    assert e["logs_job_id"], "catalogue must link to the latest job's logs"
    assert e["owner_email"] == "alice@example.com"

    # The logs link points at a real job that serves the deployment logs.
    job_resp = client.get(f"/api/v1/jobs/{e['logs_job_id']}", headers=auth_headers)
    assert job_resp.status_code == 200, job_resp.text
    assert job_resp.json()["log"] == "all good"

    # Filters: status mismatch hides the entry; severity filters work.
    assert client.get("/api/v1/catalogue?status=failed", headers=auth_headers).json() == []
    assert len(client.get("/api/v1/catalogue?severity=critical", headers=auth_headers).json()) == 1
    assert len(client.get("/api/v1/catalogue?severity=high", headers=auth_headers).json()) == 1

    # The deployment is not live yet: set it live and the status filter matches.
    with SessionLocal() as db:
        db.get(Deployment, dep_id).status = "live"
        db.commit()
    live = client.get("/api/v1/catalogue?status=live", headers=auth_headers).json()
    assert len(live) == 1 and live[0]["deployment_id"] == dep_id


def test_catalogue_scoped_to_caller_teams(client, auth_headers):
    auth_headers = auth_headers()
    pid = _ready_project(client, auth_headers, "mine")
    _deploy(client, auth_headers, pid, "orders")

    # A second user outside the team sees nothing.
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "outsider@example.com", "password": "Str0ng!Passw0rd", "password_confirm": "Str0ng!Passw0rd"},
    )
    assert resp.status_code == 201, resp.text
    login = client.post(
        "/api/v1/auth/login", json={"email": "outsider@example.com", "password": "Str0ng!Passw0rd"}
    )
    outsider = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/v1/catalogue", headers=outsider).json() == []


def test_catalogue_empty_state(client, auth_headers):
    auth_headers = auth_headers()
    assert client.get("/api/v1/catalogue", headers=auth_headers).json() == []