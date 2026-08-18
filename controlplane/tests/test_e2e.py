"""Full lifecycle test (docs/PLATFORM_SPEC.md §9, §11 Definition of done).

Drives the entire product through the HTTP API only — no direct database
writes — so it proves the API surface works as a whole rather than that the
internals can be poked into the right shape.

Only the outermost boundary is stubbed: `run_sandbox` (which would launch real
containers) and the runners built on it. Everything above that — routing, auth,
validation, repositories, Celery task orchestration, gate logic, parsers — runs
for real against a real PostgreSQL.

Gated behind the `e2e` marker; run with `pytest -m e2e`.
"""

import itertools
import json

import pytest
from controlplane.runners.scanners.base import RawResult
from controlplane.workers import tasks

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

PASSWORD = "Str0ng!Passw0rd"

SPEC = {
    "version": 1,
    "project": "lifecycle",
    "network": {"cidr": "192.168.60.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
        {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
        {"name": "worker-2", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
    ],
}

NODE_IPS = {
    "master": "192.168.60.10",
    "worker-1": "192.168.60.11",
    "worker-2": "192.168.60.12",
}

CLEAN_TRIVY = json.dumps({"Results": []})

GITLEAKS_FINDINGS = json.dumps([
    {
        "RuleID": "aws-access-token",
        "Description": "AWS Access Token",
        "File": "config/settings.py",
        "StartLine": 12,
        "Secret": "AKIAIOSFODNN7EXAMPLE",
    }
])

PIP_AUDIT_FINDINGS = json.dumps({
    "dependencies": [
        {
            "name": "requests",
            "version": "2.19.0",
            "vulns": [
                {"id": "PYSEC-2018-28", "fix_versions": ["2.20.0"], "description": "CRLF injection"}
            ],
        }
    ]
})


class _Result:
    """Stand-in for SandboxResult."""

    def __init__(self, exit_code=0, output="ok", timed_out=False):
        self.exit_code = exit_code
        self.output = output
        self.timed_out = timed_out


@pytest.fixture()
def stubbed_infra(monkeypatch, tmp_path):
    """Stub only what would touch real infrastructure."""

    clone_counter = itertools.count()

    def _fake_clone(*args, **kwargs):
        """Mimic the real _clone_repo closely enough to be safe.

        Each clone must live under its OWN parent directory: the scan task
        cleans up with `shutil.rmtree(cloned.parent)`, so a single shared
        directory would be destroyed by the first scan and break every later
        one. The repo also needs a requirements file, since pip-audit resolves
        one inside it.
        """
        parent = tmp_path / f"clone-{next(clone_counter)}"
        repo = parent / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "requirements.txt").write_text("requests==2.19.0\n")
        (repo / "Dockerfile").write_text("FROM python:3.12-slim\n")
        return repo

    def _ok(*args, **kwargs):
        return _Result()

    monkeypatch.setattr(tasks, "run_sandbox", _ok)
    monkeypatch.setattr(tasks, "terraform_init", _ok)
    monkeypatch.setattr(tasks, "terraform_apply", _ok)
    monkeypatch.setattr(tasks, "terraform_destroy", _ok)
    monkeypatch.setattr(tasks, "ansible_playbook", _ok)
    monkeypatch.setattr(tasks, "kubectl", _ok)
    monkeypatch.setattr(tasks, "kubectl_apply", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "user_ssh_private_key", lambda *a, **k: "dummy-key")
    # provision_task blocks on a real TCP wait for guest SSH (:22) on the
    # fake node IPs; no VMs exist here, so skip the wait.
    monkeypatch.setattr(tasks, "_wait_for_ssh", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "_clone_repo", _fake_clone)
    monkeypatch.setattr(
        tasks, "terraform_output",
        lambda *a, **k: _Result(output=json.dumps(NODE_IPS)),
    )
    monkeypatch.setattr(
        tasks, "run_trivy",
        lambda *a, **k: RawResult(tool="trivy", target="img", stdout=CLEAN_TRIVY),
    )
    monkeypatch.setattr(
        tasks, "run_gitleaks",
        lambda *a, **k: RawResult(tool="gitleaks", target="repo", stdout=GITLEAKS_FINDINGS),
    )
    monkeypatch.setattr(
        tasks, "run_pip_audit",
        lambda *a, **k: RawResult(tool="pip_audit", target="repo", stdout=PIP_AUDIT_FINDINGS),
    )
    return tasks


def _register_and_login(client, email):
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "password_confirm": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _run_queued_jobs(client, headers, project_id, run):
    """Execute whatever the API queued.

    The `client` fixture stubs `.apply_async`, so tasks are created but never
    dispatched to a worker. This runs them synchronously in-process, which is
    what makes the lifecycle observable inside one test.
    """
    run()
    return client


def test_full_lifecycle(client, stubbed_infra, session):
    # ---------------------------------------------------------------- 1. auth
    alice = _register_and_login(client, "alice@example.com")

    me = client.get("/api/v1/auth/me", headers=alice)
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"

    # ------------------------------------------------------------ 2. project
    created = client.post(
        "/api/v1/projects",
        json={"name": "lifecycle", "infra_spec": SPEC},
        headers=alice,
    )
    assert created.status_code == 201, created.text
    project = created.json()
    project_id = project["id"]
    assert project["status"] == "draft"
    assert len(project["nodes"]) == 3
    assert all(node["ip_address"] is None for node in project["nodes"])

    # --------------------------------------------------------- 3. provision
    provision = client.post(f"/api/v1/projects/{project_id}/provision", headers=alice)
    assert provision.status_code == 202, provision.text
    provision_job = provision.json()["job_id"]

    # The project is locked while provisioning.
    assert client.post(f"/api/v1/projects/{project_id}/provision", headers=alice).status_code == 409

    from controlplane.models import Project

    stubbed_infra.provision_task(
        provision_job, project_id, me.json()["id"], SPEC,
        str(session.get(Project, project_id).workspace_path),
    )

    job = client.get(f"/api/v1/jobs/{provision_job}", headers=alice).json()
    assert job["status"] == "succeeded", job["error_message"] or job["log"]

    detail = client.get(f"/api/v1/projects/{project_id}", headers=alice).json()
    assert detail["status"] == "ready"
    ips = {node["name"]: node["ip_address"] for node in detail["nodes"]}
    assert ips == NODE_IPS
    assert all(node["status"] == "running" for node in detail["nodes"])

    # ---------------------------------------------------------- 4. deployment
    deployed = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={
            "service_name": "users-service",
            "repo_url": "https://github.com/example/users.git",
            "branch": "main",
            "port": 8000,
            "replicas": 2,
        },
        headers=alice,
    )
    assert deployed.status_code == 201, deployed.text
    deployment_id = deployed.json()["id"]

    from controlplane.models import Job

    deploy_job = session.query(Job).filter(Job.deployment_id == deployment_id).one()
    stubbed_infra.deploy_task(
        str(deploy_job.id), deployment_id, project_id, me.json()["id"]
    )

    live = client.get(f"/api/v1/deployments/{deployment_id}", headers=alice).json()
    assert live["status"] == "live", live
    assert live["live_url"].endswith(".devops.local")
    assert live["image_ref"]

    # ---------------------------------------------------------- 5. all scans
    scans = client.post(
        f"/api/v1/projects/{project_id}/scans",
        json={"tool": "all", "target": "https://github.com/example/users.git"},
        headers=alice,
    )
    assert scans.status_code == 202, scans.text
    assert {s["tool"] for s in scans.json()} == {"trivy", "gitleaks", "pip_audit"}

    from controlplane.models import Scan

    # One job was queued per scan, in the same order. Pair them up rather than
    # reusing a single job, which would leave the later scans never executed.
    scan_jobs = (
        session.query(Job)
        .filter(Job.type == "scan", Job.project_id == project_id)
        .order_by(Job.created_at)
        .all()
    )
    assert len(scan_jobs) == 3
    for scan, scan_job in zip(scans.json(), scan_jobs, strict=True):
        stubbed_infra.scan_task(
            str(scan_job.id), scan["id"], project_id, scan["tool"], scan["target"]
        )

    session.expire_all()
    completed = session.query(Scan).filter(Scan.project_id == project_id).all()
    assert len(completed) == 3
    assert all(s.status == "completed" for s in completed), [(s.tool, s.status, (s.raw_output or {})) for s in completed]

    # ------------------------------------------------------ 6. security report
    summary = client.get(f"/api/v1/projects/{project_id}/security/summary", headers=alice)
    assert summary.status_code == 200
    report = summary.json()

    # Gitleaks found a secret and pip-audit found a vulnerable dependency, so
    # the report must not be empty — an all-zero summary here would mean the
    # parsers silently dropped findings.
    assert sum(report["current"].values()) > 0, report
    assert report["top_issues"], report

    findings_seen = []
    for scan in client.get(f"/api/v1/projects/{project_id}/scans", headers=alice).json():
        page = client.get(f"/api/v1/scans/{scan['id']}/findings", headers=alice).json()
        findings_seen.extend(page["items"])
    identifiers = {f["identifier"] for f in findings_seen}
    assert "aws-access-token" in identifiers or "PYSEC-2018-28" in identifiers, identifiers

    # --------------------------------------------------- 7. tenant isolation
    bob = _register_and_login(client, "bob@example.com")

    assert client.get("/api/v1/projects", headers=bob).json() == []
    for path in (
        f"/api/v1/projects/{project_id}",
        f"/api/v1/projects/{project_id}/nodes",
        f"/api/v1/projects/{project_id}/deployments",
        f"/api/v1/projects/{project_id}/scans",
        f"/api/v1/projects/{project_id}/security/summary",
        f"/api/v1/deployments/{deployment_id}",
        f"/api/v1/jobs/{provision_job}",
    ):
        resp = client.get(path, headers=bob)
        # 404, never 403 — a 403 would confirm the resource exists (§7.3).
        assert resp.status_code == 404, f"{path} leaked to another tenant: {resp.status_code}"

    # Bob cannot destroy Alice's project either.
    assert client.post(
        f"/api/v1/projects/{project_id}/destroy",
        json={"confirm_name": "lifecycle"},
        headers=bob,
    ).status_code == 404

    # ------------------------------------------------------------ 8. destroy
    # A wrong confirmation name must not destroy anything.
    assert client.post(
        f"/api/v1/projects/{project_id}/destroy",
        json={"confirm_name": "wrong-name"},
        headers=alice,
    ).status_code == 422
    assert client.get(f"/api/v1/projects/{project_id}", headers=alice).status_code == 200

    destroy = client.post(
        f"/api/v1/projects/{project_id}/destroy",
        json={"confirm_name": "lifecycle"},
        headers=alice,
    )
    assert destroy.status_code == 202, destroy.text
    destroy_job = destroy.json()["job_id"]

    workspace = session.get(Project, project_id).workspace_path
    stubbed_infra.destroy_task(
        destroy_job, project_id, str(workspace), "lifecycle", me.json()["id"]
    )

    final = client.get(f"/api/v1/jobs/{destroy_job}", headers=alice).json()
    assert final["status"] == "succeeded", final["error_message"] or final["log"]

    session.expire_all()
    destroyed = session.get(Project, project_id)
    assert destroyed is None or destroyed.status == "destroyed", (
        destroyed.status if destroyed else None
    )
