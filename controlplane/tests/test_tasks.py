"""Celery task logic with runners stubbed — exercises the orchestration,
gate logic and error handling without touching real infra (docs/PLATFORM_SPEC.md
§7, phases F1-F7)."""

import json

import pytest
from controlplane.core.security import hash_password
from controlplane.models import Deployment, Job, Project, Scan, User
from controlplane.runners.scanners.base import RawResult
from controlplane.workers import tasks

pytestmark = pytest.mark.integration

SPEC = {
    "version": 1,
    "project": "my-cluster",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
        {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
    ],
}

TRIVY_OK = json.dumps({"Results": []})
TRIVY_BLOCKED = json.dumps(
    {
        "Results": [
            {"Target": "img", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-2024-1", "Severity": "CRITICAL", "PkgName": "x", "Title": "t"}
            ]}
        ]
    }
)


@pytest.fixture()
def user(session):
    user = User(email="alice@example.com", password_hash=hash_password("Sup3rSecret!"), role="user")
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def team(session, user):
    from controlplane.repositories.teams import ensure_personal_team

    return ensure_personal_team(session, user)


@pytest.fixture()
def project(session, user, team):
    project = Project(owner_id=user.id, team_id=team.id, name="my-cluster", infra_spec=SPEC, status="draft")
    session.add(project)
    session.flush()
    # Nodes are normally created alongside the project by POST /projects;
    # provision_task only fills in their IPs, so they must already exist.
    from controlplane.models import Node

    for node in SPEC["nodes"]:
        session.add(
            Node(
                project_id=project.id,
                name=node["name"],
                vcpu=node["vcpu"],
                memory_mb=node["memory_mb"],
                disk_gb=node["disk_gb"],
                role=node["role"],
            )
        )
    session.commit()
    return project


class _StubResult:
    def __init__(self, exit_code=0, output="", timed_out=False):
        self.exit_code = exit_code
        self.output = output
        self.timed_out = timed_out


@pytest.fixture()
def stub_runners(monkeypatch):
    """Replace every runner the tasks touch with deterministic stubs."""

    def _ok(*args, **kwargs):
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "render_terraform", lambda *a, **k: [])
    monkeypatch.setattr(tasks, "render_ansible", lambda *a, **k: [])
    monkeypatch.setattr(tasks, "terraform_init", _ok)
    monkeypatch.setattr(tasks, "terraform_apply", _ok)
    monkeypatch.setattr(tasks, "terraform_output", lambda *a, **k: _StubResult(exit_code=0, output='{"master": "192.168.56.10", "worker-1": "192.168.56.11"}'))
    monkeypatch.setattr(tasks, "terraform_destroy", _ok)
    monkeypatch.setattr(tasks, "ansible_playbook", _ok)
    monkeypatch.setattr(tasks, "user_ssh_private_key", lambda *a, **k: "dummy-key")
    monkeypatch.setattr(tasks, "run_trivy", lambda *a, **k: RawResult(tool="trivy", target="img", stdout=TRIVY_OK))
    monkeypatch.setattr(tasks, "run_gitleaks", lambda *a, **k: RawResult(tool="gitleaks", target="x"))
    monkeypatch.setattr(tasks, "run_pip_audit", lambda *a, **k: RawResult(tool="pip_audit", target="x"))
    monkeypatch.setattr(tasks, "_clone_repo", lambda *a, **k: __import__("pathlib").Path("/tmp/fake-repo"))
    # deploy_task shells out through run_sandbox for `docker build` and
    # `docker push`; without stubbing it the task fails long before reaching
    # the trivy gate and rollout logic these tests are actually asserting on.
    monkeypatch.setattr(tasks, "run_sandbox", _ok)
    return tasks


def _new_job(session, project, job_type):
    job = Job(project_id=project.id, type=job_type, status="queued")
    session.add(job)
    session.commit()
    return job


def test_provision_task_happy_path(session, project, user, stub_runners):
    job = _new_job(session, project, "provision")
    stub_runners.provision_task(str(job.id), str(project.id), str(user.id), SPEC, "/tmp/ws")

    session.refresh(job)
    session.refresh(project)
    assert job.status == "succeeded"
    assert project.status == "ready"
    # node IPs captured and recorded
    from controlplane.models import Node

    ips = {n.name: n.ip_address for n in session.query(Node).filter(Node.project_id == project.id)}
    assert ips["master"] == "192.168.56.10"
    assert ips["worker-1"] == "192.168.56.11"


def test_provision_apply_failure_attempts_cleanup(session, project, user, stub_runners, monkeypatch):
    calls = []

    def _failing_apply(*a, **k):
        calls.append("apply")
        raise RuntimeError("libvirt connect failed")

    monkeypatch.setattr(tasks, "terraform_apply", _failing_apply)
    job = _new_job(session, project, "provision")
    stub_runners.provision_task(str(job.id), str(project.id), str(user.id), SPEC, "/tmp/ws")

    session.refresh(job)
    session.refresh(project)
    assert job.status == "failed"
    assert project.status == "failed"
    assert "apply" in calls
    # cleanup attempted
    assert "cleanup" in job.log.lower() or "destroy" in job.log.lower()


def test_scan_task_persists_findings(session, project, user, stub_runners):
    scan = Scan(project_id=project.id, tool="trivy", target="users-service:1.0.0", status="queued")
    session.add(scan)
    session.commit()
    job = _new_job(session, project, "scan")

    stub_runners.scan_task(str(job.id), str(scan.id), str(project.id), "trivy", "users-service:1.0.0")

    session.refresh(job)
    session.refresh(scan)
    assert job.status == "succeeded"
    assert scan.status == "completed"
    assert scan.summary == {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}


def test_scan_task_rejects_evil_target(session, project, user, stub_runners, monkeypatch):
    def _no_clone(*a, **k):
        raise ValueError("Repository URL scheme must be https")

    monkeypatch.setattr(tasks, "_clone_repo", _no_clone)
    scan = Scan(project_id=project.id, tool="gitleaks", target="git@github.com:org/repo.git", status="queued")
    session.add(scan)
    session.commit()
    job = _new_job(session, project, "scan")

    stub_runners.scan_task(str(job.id), str(scan.id), str(project.id), "gitleaks", "git@github.com:org/repo.git")

    session.refresh(job)
    session.refresh(scan)
    assert job.status == "failed"
    assert scan.status == "failed"


def test_deploy_task_blocks_on_critical(session, project, user, stub_runners, monkeypatch):
    deployment = Deployment(
        project_id=project.id,
        service_name="users-service",
        repo_url="https://github.com/org/users.git",
        branch="main",
        port=8000,
        replicas=2,
        status="queued",
    )
    session.add(deployment)
    session.commit()
    job = Job(project_id=project.id, deployment_id=deployment.id, type="deploy", status="queued")
    session.add(job)
    session.commit()

    def _blocking_trivy(*a, **k):
        return RawResult(tool="trivy", target="img", stdout=TRIVY_BLOCKED)

    monkeypatch.setattr(tasks, "run_trivy", _blocking_trivy)

    stub_runners.deploy_task(str(job.id), str(deployment.id), str(project.id), str(user.id))

    session.refresh(job)
    session.refresh(deployment)
    assert job.status == "failed"
    assert deployment.status == "blocked"
    assert "blocked" in job.error_message.lower() or "critical" in job.error_message.lower()


def test_deploy_task_gates_pass_when_clean(session, project, user, stub_runners, monkeypatch):
    deployment = Deployment(
        project_id=project.id,
        service_name="users-service",
        repo_url="https://github.com/org/users.git",
        branch="main",
        port=8000,
        replicas=2,
        status="queued",
    )
    session.add(deployment)
    session.commit()
    job = Job(project_id=project.id, deployment_id=deployment.id, type="deploy", status="queued")
    session.add(job)
    session.commit()

    def _fake_kubectl(args, *a, **k):
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "kubectl", _fake_kubectl)
    monkeypatch.setattr(tasks, "kubectl_apply", lambda *a, **k: None)

    stub_runners.deploy_task(str(job.id), str(deployment.id), str(project.id), str(user.id))

    session.refresh(job)
    session.refresh(deployment)
    assert job.status == "succeeded"
    assert deployment.status == "live"
    assert deployment.live_url.endswith(".devops.local")


def test_undeploy_task_deletes_manifests(session, project, user, stub_runners, monkeypatch):
    calls = []

    def _fake_kubectl(args, *a, **k):
        calls.append(args)
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "kubectl", _fake_kubectl)
    job = _new_job(session, project, "deploy")
    stub_runners.undeploy_task(str(job.id), "users-service", str(project.id), str(user.id))

    session.refresh(job)
    assert job.status == "succeeded"
    # kubectl is called as ["delete", <kind>, <name>, "--namespace=<ns>", ...]
    kinds = [call[1] for call in calls]
    assert kinds == ["deployment", "service", "ingress"]
    ns = tasks.k8s_namespace(project.id)
    assert all(f"--namespace={ns}" in call for call in calls)


def test_cancelled_job_is_noop(session, project, user, stub_runners):
    job = _new_job(session, project, "provision")
    job.cancel_requested = True
    session.commit()
    stub_runners.provision_task(str(job.id), str(project.id), str(user.id), SPEC, "/tmp/ws")
    session.refresh(job)
    assert job.status == "queued"  # untouched


def test_find_requirements_prefers_repo_root():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "requirements.txt").write_text("flask\n")
        (repo / "app").mkdir()
        (repo / "app" / "requirements.txt").write_text("django\n")
        assert tasks._find_requirements(repo).name == "requirements.txt"


def test_find_requirements_raises_when_missing():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(FileNotFoundError):
            tasks._find_requirements(Path(tmp))


def test_safe_json_handles_malformed():
    assert tasks._safe_json("not json") == {"raw": "not json"}
    assert tasks._safe_json('{"a": 1}') == {"a": 1}


def test_append_log_truncates_keeping_tail(session, project, user):
    from controlplane.workers.tasks import _LOG_CAP, _append_log

    job = Job(project_id=project.id, type="provision", status="queued")
    session.add(job)
    session.commit()

    head = "x" * 150_000
    tail = "y" * 150_000
    _append_log(job.id, head)
    _append_log(job.id, tail)

    session.expire_all()
    job = session.get(Job, job.id)
    assert len(job.log) <= _LOG_CAP
    assert "truncated" in job.log
    assert job.log.rstrip().endswith("y" * 5000)
    assert not job.log.lstrip().startswith("x")
    assert job.log.count("truncated") == 1


def test_destroy_task_never_touches_a_differently_owned_same_named_namespace(
    session, user, team, monkeypatch
):
    """The bug Phase 2 (multi-tenancy plan) closes: two teams each naming a
    project "staging" must not share a namespace, and destroying one must
    never issue a `kubectl delete namespace` that could reach the other's —
    project names are unique only per team (models/project.py), not
    globally."""
    from controlplane.core.security import hash_password
    from controlplane.core.validation import k8s_namespace
    from controlplane.models import User
    from controlplane.repositories.teams import ensure_personal_team

    ns_spec = {"version": 1, "project": "staging", "mode": "namespace", "network": {}, "nodes": []}

    project_a = Project(owner_id=user.id, team_id=team.id, name="staging", status="ready", infra_spec=ns_spec)
    session.add(project_a)
    session.flush()

    other_user = User(email="other-team@example.com", password_hash=hash_password("Sup3rSecret!"))
    session.add(other_user)
    session.flush()
    other_team = ensure_personal_team(session, other_user)
    project_b = Project(owner_id=other_user.id, team_id=other_team.id, name="staging", status="ready", infra_spec=ns_spec)
    session.add(project_b)
    session.commit()

    ns_a = k8s_namespace(project_a.id)
    ns_b = k8s_namespace(project_b.id)
    assert ns_a != ns_b

    deleted = []

    def _fake_kubectl(args, *a, **k):
        if args[:2] == ["delete", "namespace"]:
            deleted.append(args[2])
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "kubectl", _fake_kubectl)

    job = Job(project_id=project_a.id, type="destroy", status="queued")
    session.add(job)
    session.commit()

    tasks.destroy_task(str(job.id), str(project_a.id), "", "staging", str(user.id))

    assert deleted == [ns_a]
    assert ns_b not in deleted


def test_destroy_task_completes_when_project_row_already_deleted(
    session, user, team, monkeypatch
):
    """DELETE /projects/{id} queues the destroy job *and* deletes the row, so
    the worker races the API. Found end-to-end: the teardown succeeded but
    the final status update matched 0 rows, poisoning the session and leaving
    the job stuck in "running" forever."""
    ns_spec = {"version": 1, "project": "doomed", "mode": "namespace", "network": {}, "nodes": []}
    project = Project(owner_id=user.id, team_id=team.id, name="doomed", status="destroying", infra_spec=ns_spec)
    session.add(project)
    session.commit()
    project_id = project.id

    job = Job(project_id=project_id, type="destroy", status="queued")
    session.add(job)
    session.commit()
    job_id = job.id

    def _fake_kubectl(args, *a, **k):
        # Simulate the API deleting the row while the teardown is in flight.
        from controlplane.db import SessionLocal

        with SessionLocal() as other:
            other.query(Job).filter(Job.id == job_id).update({"project_id": None})
            other.query(Project).filter(Project.id == project_id).delete()
            other.commit()
        return _StubResult(exit_code=0, output="ok")

    monkeypatch.setattr(tasks, "kubectl", _fake_kubectl)

    # Must not raise, and must mark the job finished rather than hanging.
    tasks.destroy_task(str(job_id), str(project_id), "", "doomed", str(user.id))

    session.expire_all()
    assert session.get(Job, job_id).status == "succeeded"
