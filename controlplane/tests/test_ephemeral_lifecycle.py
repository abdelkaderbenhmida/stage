"""Ephemeral-environment lifecycle tests (docs/TODO.md Tasks 2.1-2.5).

Covers the fast-path product surface: presets, namespace mode, the TTL
reaper + extend endpoint, Git webhooks, and warm-pool adoption. API-level
tests run through FastAPI TestClient; task-level tests use the same stubbed
runners as test_tasks.py so no real infrastructure is touched.
"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from controlplane.core.pool import spec_hash
from controlplane.core.security import hash_password
from controlplane.models import Job, Node, PooledCluster, Project, User
from controlplane.workers import tasks

pytestmark = pytest.mark.integration

VM_SPEC = {
    "version": 1,
    "project": "my-cluster",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
    ],
}

NS_SPEC = {
    "version": 1,
    "project": "fast-path",
    "mode": "namespace",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
    ],
}


@pytest.fixture()
def auth(auth_headers):
    return auth_headers()


@pytest.fixture()
def user(session):
    user = User(email="ephemeral@example.com", password_hash=hash_password("Sup3rSecret!"))
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def project(session, user):
    project = Project(owner_id=user.id, name="my-cluster", infra_spec=VM_SPEC, status="ready")
    session.add(project)
    session.flush()
    for node in VM_SPEC["nodes"]:
        session.add(Node(project_id=project.id, name=node["name"], vcpu=node["vcpu"],
                         memory_mb=node["memory_mb"], disk_gb=node["disk_gb"], role=node["role"]))
    session.commit()
    return project


class _StubResult:
    def __init__(self, exit_code=0, output="ok", timed_out=False):
        self.exit_code = exit_code
        self.output = output
        self.timed_out = timed_out


@pytest.fixture()
def stub_runners(monkeypatch):
    """Same deterministic stubs as test_tasks.py plus a captured kubectl_apply."""

    def _ok(*a, **k):
        return _StubResult(exit_code=0)

    calls = {"kubectl": []}

    def _kubectl_apply(*a, **k):
        calls["kubectl"].append(a)
        return _ok(*a, **k)

    monkeypatch.setattr(tasks, "render_terraform", lambda *a, **k: [])
    monkeypatch.setattr(tasks, "render_ansible", lambda *a, **k: [])
    monkeypatch.setattr(tasks, "terraform_init", _ok)
    monkeypatch.setattr(tasks, "terraform_apply", _ok)
    monkeypatch.setattr(tasks, "terraform_output", lambda *a, **k: _StubResult(exit_code=0, output="{}"))
    monkeypatch.setattr(tasks, "terraform_destroy", _ok)
    monkeypatch.setattr(tasks, "ansible_playbook", _ok)
    monkeypatch.setattr(tasks, "user_ssh_private_key", lambda *a, **k: "dummy")
    monkeypatch.setattr(tasks, "kubectl_apply", _kubectl_apply)
    monkeypatch.setattr(tasks, "run_sandbox", _ok)
    return calls


def _new_job(session, project, job_type):
    job = Job(project_id=project.id, type=job_type, status="queued")
    session.add(job)
    session.commit()
    return job


# ---------------------------------------------------------------------------
# Task 2.1 — presets
# ---------------------------------------------------------------------------


def _create_project(client, headers, name="preset-app", payload=None):
    return client.post("/api/v1/projects", json={"name": name, **(payload or {})}, headers=headers)


def test_create_project_with_preset(client, auth):
    resp = _create_project(client, auth, payload={"preset": "small"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    spec = body["infra_spec"]
    assert [n["name"] for n in spec["nodes"]] == ["master"]
    assert spec["nodes"][0]["vcpu"] == 2


def test_create_project_preset_and_nodes_rejected(client, auth):
    resp = _create_project(client, auth, payload={"preset": "small", "infra_spec": VM_SPEC})
    assert resp.status_code == 422


def test_create_project_unknown_preset_rejected(client, auth):
    resp = _create_project(client, auth, payload={"preset": "huge"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 2.3 — namespace mode fast path
# ---------------------------------------------------------------------------


def test_provision_namespace_mode_skips_terraform(session, user, stub_runners):
    project = Project(owner_id=user.id, name="fast-path", infra_spec=NS_SPEC, status="draft")
    session.add(project)
    session.flush()
    for node in NS_SPEC["nodes"]:
        session.add(Node(project_id=project.id, name=node["name"], vcpu=node["vcpu"],
                         memory_mb=node["memory_mb"], disk_gb=node["disk_gb"], role=node["role"]))
    session.commit()
    calls: dict = stub_runners
    terraform_calls = []
    import controlplane.workers.tasks as t

    def _boom(*a, **k):
        terraform_calls.append(a)
        raise AssertionError("terraform must not run in namespace mode")

    for name in ("terraform_init", "terraform_apply", "ansible_playbook"):
        monkeypatch = __import__("pytest").MonkeyPatch()
        monkeypatch.setattr(t, name, _boom)
    monkeypatch = __import__("pytest").MonkeyPatch()
    job = _new_job(session, project, "provision")
    tasks.provision_task(str(job.id), str(project.id), str(user.id), NS_SPEC, "/tmp/ns-ws")

    assert terraform_calls == [], "terraform/ansible ran in namespace mode"
    session.refresh(project)
    assert project.status == "ready"
    assert project.expires_at is not None
    assert len(calls["kubectl"]) == 1
    job = session.query(Job).filter(Job.project_id == project.id).one()
    session.refresh(job)  # task wrote via another session; identity map is stale
    assert job.status == "succeeded"


def test_create_project_namespace_mode_via_api(client, auth):
    resp = _create_project(client, auth, name="ns-app", payload={"infra_spec": NS_SPEC})
    assert resp.status_code == 201
    assert resp.json()["infra_spec"]["mode"] == "namespace"


# ---------------------------------------------------------------------------
# Task 2.2 — TTL: extend endpoint and the reaper
# ---------------------------------------------------------------------------


def test_extend_pushes_deadline(client, auth):
    created = client.post(
        "/api/v1/projects", json={"name": "ext-me", "infra_spec": VM_SPEC}, headers=auth
    )
    pid = created.json()["id"]
    resp = client.post(f"/api/v1/projects/{pid}/extend", json={"hours": 5}, headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ttl_hours"] == 29  # default 24 + 5

    from controlplane.db import SessionLocal

    with SessionLocal() as db:
        stored = db.get(Project, pid)
        assert stored.expires_at > datetime.now(UTC)


def test_extend_respects_max_ttl_ceiling(client, auth):
    created = client.post(
        "/api/v1/projects", json={"name": "ext-cap", "infra_spec": VM_SPEC}, headers=auth
    )
    pid = created.json()["id"]
    from controlplane.db import SessionLocal

    with SessionLocal() as db:
        stored = db.get(Project, pid)
        stored.created_at = datetime.now(UTC) - timedelta(days=6)
        db.commit()
    resp = client.post(f"/api/v1/projects/{pid}/extend", json={"hours": 168}, headers=auth)
    assert resp.status_code == 409, resp.text
    assert "capped" in resp.json()["detail"]


def test_reap_warns_then_destroys_expired(session, user, monkeypatch):
    project = Project(
        owner_id=user.id, name="expiring", infra_spec=VM_SPEC, status="ready",
        auto_destroy=True, ttl_hours=24,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        workspace_path="/tmp/ws-expiring",
    )
    warned = Project(
        owner_id=user.id, name="warned", infra_spec=VM_SPEC, status="ready",
        auto_destroy=True, ttl_hours=24,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    sticky = Project(
        owner_id=user.id, name="sticky", infra_spec=VM_SPEC, status="ready",
        auto_destroy=False, ttl_hours=24,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    session.add_all([project, warned, sticky])
    session.commit()

    queued = []

    class _Stub:
        id = "fake-task-id"

    def _fake_apply_async(*a, **k):
        queued.append(k["args"][0])
        return _Stub()

    monkeypatch.setattr(tasks.destroy_task, "apply_async", _fake_apply_async)

    result = tasks.reap_expired_projects()
    assert result == {"warned": 1, "reaped": 1}

    session.refresh(project)
    session.refresh(warned)
    session.refresh(sticky)
    assert project.status == "destroying"
    assert len(queued) == 1
    queued_job = (
        session.query(Job)
        .filter(Job.project_id == project.id, Job.type == "destroy")
        .one()
    )
    assert str(queued_job.id) == queued[0]
    assert warned.expiry_warned is True
    assert warned.status == "ready"
    assert sticky.status == "ready"

    from controlplane.models import AuditLog

    log = session.query(AuditLog).filter(AuditLog.action == "project.reaped").all()
    assert any(entry.resource_id == str(project.id) for entry in log)

    # Idempotent: a second run reaps nothing.
    assert tasks.reap_expired_projects() == {"warned": 0, "reaped": 0}


def test_reap_skips_project_with_active_destroy_job(session, user):
    project = Project(
        owner_id=user.id, name="busy", infra_spec=VM_SPEC, status="ready",
        auto_destroy=True, ttl_hours=24,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
        workspace_path="/tmp/ws-busy",
    )
    session.add(project)
    session.commit()
    running = Job(project_id=project.id, type="destroy", status="running")
    session.add(running)
    session.commit()

    result = tasks.reap_expired_projects()
    assert result == {"warned": 0, "reaped": 0}
    session.refresh(project)
    assert project.status == "ready"
    assert session.query(Job).filter(Job.project_id == project.id).count() == 1


def test_reaper_two_workers_never_double_destroy(session, user, monkeypatch):
    """Two reapers racing on the same beat schedule must destroy each
    expired project at most once (§7 item 7). On PostgreSQL the scan uses
    FOR UPDATE SKIP LOCKED, so worker B's scan skips the row worker A is
    holding; the count-then-act window disappears entirely."""
    import threading

    project = Project(
        owner_id=user.id, name="race", infra_spec=VM_SPEC, status="ready",
        auto_destroy=True, ttl_hours=24,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
        workspace_path="/tmp/ws-race",
    )
    session.add(project)
    session.commit()

    queued = []
    worker_a_locked = threading.Event()
    worker_b_scanned = threading.Event()

    class _Stub:
        id = "fake-task-id"

    def fake_apply_async(*a, **k):
        queued.append(k["args"][0])
        # Worker A's reaper transaction is still open and holds the project
        # row FOR UPDATE. Signal worker B to scan NOW, then hold A until B
        # finished — so B's SKIP LOCKED scan runs while the lock is held,
        # deterministically, with no timing dependency on sleeps.
        worker_a_locked.set()
        assert worker_b_scanned.wait(timeout=30)
        return _Stub()

    monkeypatch.setattr(tasks.destroy_task, "apply_async", fake_apply_async)

    results: dict[str, dict | Exception] = {}

    def _worker(name, fn):
        try:
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001
            results[name] = exc

    worker_a = threading.Thread(target=_worker, args=("a", tasks.reap_expired_projects))
    worker_a.start()
    assert worker_a_locked.wait(timeout=15)

    results["b"] = tasks.reap_expired_projects()
    worker_b_scanned.set()
    worker_a.join(timeout=60)

    assert results["a"] == {"warned": 0, "reaped": 1}
    assert results["b"] == {"warned": 0, "reaped": 0}
    assert len(queued) == 1
    assert (
        session.query(Job)
        .filter(Job.project_id == project.id, Job.type == "destroy")
        .count()
        == 1
    )
    session.refresh(project)
    assert project.status == "destroying"


# ---------------------------------------------------------------------------
# Task 2.4 — webhooks
# ---------------------------------------------------------------------------


@pytest.fixture()
def deployment(client, auth):
    created = client.post(
        "/api/v1/projects",
        json={"name": "wh-project", "infra_spec": VM_SPEC},
        headers=auth,
    )
    pid = created.json()["id"]
    from controlplane.db import SessionLocal
    from controlplane.models import Project

    with SessionLocal() as db:
        db.get(Project, pid).status = "ready"
        db.commit()

    resp = client.post(
        f"/api/v1/projects/{pid}/deployments",
        json={"service_name": "users", "repo_url": "https://github.com/org/users.git", "port": 8000},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return pid, resp.json()["id"], auth


def _subscribe(client, auth, deployment_id, branch="main", provider="github", pr=None):
    payload = {"provider": provider, "branch": branch}
    if pr is not None:
        payload["pull_request_number"] = pr
    resp = client.post(
        f"/api/v1/deployments/{deployment_id}/webhook",
        json=payload,
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["secret"]


def _github_body(branch="main", action=None, repo="https://github.com/org/users.git"):
    payload = {"ref": f"refs/heads/{branch}", "repository": {"clone_url": repo}}
    if action is not None:
        payload["action"] = action
    return json.dumps(payload).encode()


def _sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_valid_push_queues_redeploy(client, deployment, monkeypatch):
    pid, dep_id, auth = deployment
    secret = _subscribe(client, auth, dep_id)
    queued = []
    monkeypatch.setattr(tasks, "queue_deploy", lambda *a, **k: queued.append(a) or None)

    body = _github_body()
    resp = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(secret, body),
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "Deploy queued."
    assert len(queued) == 1


def test_webhook_bad_signature_rejected(client, deployment, monkeypatch):
    pid, dep_id, auth = deployment
    _subscribe(client, auth, dep_id)
    queued = []
    monkeypatch.setattr(tasks, "queue_deploy", lambda *a, **k: queued.append(a[0]) or None)

    body = _github_body()
    resp = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64, "X-GitHub-Event": "push"},
    )
    assert resp.status_code == 401
    assert queued == []


def test_webhook_missing_signature_rejected(client, deployment):
    resp = client.post(
        "/api/v1/webhooks/github",
        content=_github_body(),
        headers={"X-GitHub-Event": "push"},
    )
    assert resp.status_code == 401


def test_webhook_wrong_branch_ignored(client, deployment):
    pid, dep_id, auth = deployment
    secret = _subscribe(client, auth, dep_id)
    body = _github_body(branch="develop")
    resp = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _sign(secret, body), "X-GitHub-Event": "push"},
    )
    assert resp.status_code == 200
    assert "Ignored" in resp.json()["message"]


def test_webhook_wrong_repo_ignored(client, deployment):
    pid, dep_id, auth = deployment
    secret = _subscribe(client, auth, dep_id)
    body = _github_body(repo="https://github.com/evil/other.git")
    resp = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _sign(secret, body), "X-GitHub-Event": "push"},
    )
    assert resp.status_code == 200
    assert "Ignored" in resp.json()["message"]


def test_webhook_gitlab_token_verification(client, deployment, monkeypatch):
    pid, dep_id, auth = deployment
    secret = _subscribe(client, auth, dep_id, provider="gitlab")
    queued = []
    monkeypatch.setattr(tasks, "queue_deploy", lambda *a, **k: queued.append(a[0]) or None)

    body = _github_body()
    resp = client.post(
        "/api/v1/webhooks/gitlab",
        content=body,
        headers={"X-Gitlab-Token": secret, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    assert len(queued) == 1

    resp = client.post(
        "/api/v1/webhooks/gitlab",
        content=body,
        headers={"X-Gitlab-Token": "wrong-token"},
    )
    assert resp.status_code == 401


def test_webhook_closed_pr_destroys_environment(client, deployment, monkeypatch):
    pid, dep_id, auth = deployment
    secret = _subscribe(client, auth, dep_id, branch="feature/x", pr=42)
    destroyed = []
    monkeypatch.setattr(
        tasks, "queue_destroy", lambda *a, **k: destroyed.append(a[0]) or None
    )

    body = json.dumps(
        {
            "ref": "refs/heads/feature/x",
            "action": "closed",
            "repository": {"clone_url": "https://github.com/org/users.git"},
        }
    ).encode()
    resp = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _sign(secret, body), "X-GitHub-Event": "pull_request"},
    )
    assert resp.status_code == 200
    assert destroyed == [uuid.UUID(pid)]


def test_webhook_rate_limited(client, deployment, monkeypatch):
    pid, dep_id, auth = deployment
    secret = _subscribe(client, auth, dep_id)
    body = _github_body()

    # The limiter allows 120 deliveries/min per source; trip it deterministically
    # rather than hammering the endpoint (the limiter itself is covered in
    # test_rate_limit.py).
    import controlplane.api.routers.webhooks as webhooks

    calls = {"n": 0}

    def _flaky_limit(*a, **k):
        calls["n"] += 1
        return calls["n"] < 3

    monkeypatch.setattr(webhooks, "check_rate_limit", _flaky_limit)
    for _ in range(5):
        resp = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": _sign(secret, body), "X-GitHub-Event": "push"},
        )
        if resp.status_code == 429:
            break
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Task 2.5 — warm pool adoption
# ---------------------------------------------------------------------------


def test_provision_adopts_warm_cluster(session, user, stub_runners):
    project = Project(owner_id=user.id, name="pooled-app", infra_spec=VM_SPEC, status="draft")
    session.add(project)
    session.flush()
    for node in VM_SPEC["nodes"]:
        session.add(Node(project_id=project.id, name=node["name"], vcpu=node["vcpu"],
                         memory_mb=node["memory_mb"], disk_gb=node["disk_gb"], role=node["role"]))
    session.commit()

    from controlplane.schemas.spec import InfraSpec

    fingerprint = spec_hash(InfraSpec.model_validate(VM_SPEC))
    pooled = PooledCluster(
        spec_hash=fingerprint,
        status="available",
        workspace_path="/ws/pooled",
        node_ips=json.dumps({"master": "192.168.56.99"}),
    )
    session.add(pooled)
    session.commit()

    terraform_runs = []

    def _counting_apply(*a, **k):
        terraform_runs.append(a)
        return _StubResult(exit_code=0)

    stub_runners["apply"] = terraform_runs
    import controlplane.workers.tasks as t

    monkeypatch_holder = __import__("pytest").MonkeyPatch()
    monkeypatch_holder.setattr(t, "terraform_apply", _counting_apply)
    try:
        job = _new_job(session, project, "provision")
        tasks.provision_task(str(job.id), str(project.id), str(user.id), VM_SPEC, "/tmp/ws")
    finally:
        monkeypatch_holder.undo()

    session.refresh(project)
    assert project.status == "ready"
    assert project.workspace_path == "/ws/pooled"
    assert terraform_runs == [], "terraform ran despite a warm cluster"

    from controlplane.models import Node as NodeModel

    ips = {n.name: n.ip_address for n in session.query(NodeModel).filter(NodeModel.project_id == project.id)}
    assert ips["master"] == "192.168.56.99"
    job = session.query(Job).filter(Job.project_id == project.id).one()
    session.refresh(job)  # task wrote via another session; identity map is stale
    assert job.status == "succeeded"


def test_provision_warm_pool_mismatch_falls_back_to_terraform(session, user, stub_runners):
    project = Project(owner_id=user.id, name="cold-app", infra_spec=VM_SPEC, status="draft")
    session.add(project)
    session.flush()
    for node in VM_SPEC["nodes"]:
        session.add(Node(project_id=project.id, name=node["name"], vcpu=node["vcpu"],
                         memory_mb=node["memory_mb"], disk_gb=node["disk_gb"], role=node["role"]))
    session.commit()

    OTHER_SPEC = {
        **VM_SPEC,
        "project": "cold-app",
        "nodes": [
            {"name": "master", "vcpu": 8, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
        ],
    }

    from controlplane.schemas.spec import InfraSpec

    fingerprint = spec_hash(InfraSpec.model_validate(OTHER_SPEC))
    session.add(PooledCluster(spec_hash=fingerprint, status="available",
                              workspace_path="/ws/wrong-shape"))
    session.commit()

    runs = []

    def _capture_apply(*a, **k):
        runs.append("apply")
        return _StubResult(exit_code=0)

    import controlplane.workers.tasks as t

    monkeypatch_holder = __import__("pytest").MonkeyPatch()
    monkeypatch_holder.setattr(t, "terraform_apply", _capture_apply)
    try:
        job = _new_job(session, project, "provision")
        tasks.provision_task(str(job.id), str(project.id), str(user.id), VM_SPEC, "/tmp/ws")
    finally:
        monkeypatch_holder.undo()

    session.refresh(project)
    assert project.status == "ready"
    assert runs == ["apply"]  # fell back to normal provisioning