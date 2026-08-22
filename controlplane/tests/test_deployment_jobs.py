"""Per-deployment pipeline history, and the one-deploy-at-a-time rule.

Every deploy was already recorded against its deployment, but nothing could
read those rows back, and nothing stopped a second deploy starting while the
first was still running — two builds of the same service pushing under the
same tag prefix and applying the same manifests, with the last one to finish
winning by accident.
"""

import json
import uuid

import pytest
import sqlalchemy as sa
from controlplane.models import Job, Project, User
from controlplane.repositories.base import NotFoundError, Scope
from controlplane.repositories.jobs import JobRepository
from controlplane.workers import tasks

pytestmark = pytest.mark.integration

NS_SPEC = {
    "version": 1,
    "project": "history",
    "mode": "namespace",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
    ],
}


@pytest.fixture()
def deployment(client, auth_headers):
    """A ready project with one deployment, created through the API."""
    auth = auth_headers(email="history@example.com")
    resp = client.post(
        "/api/v1/projects",
        json={"name": "history", "infra_spec": NS_SPEC},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]

    from controlplane.db import SessionLocal

    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(project_id))
        project.status = "ready"
        db.commit()

    resp = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={
            "service_name": "api",
            "repo_url": "https://github.com/org/api.git",
            "branch": "main",
            "port": 8080,
        },
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    return project_id, resp.json()["id"], auth


def _finish(deployment_id: str, status: str = "succeeded") -> None:
    """Terminate the deploy job in flight, as a worker would."""
    from controlplane.db import SessionLocal

    with SessionLocal() as db:
        job = tasks.active_deploy_job(db, uuid.UUID(deployment_id))
        assert job is not None
        job.status = status
        db.commit()


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_history_lists_every_run_newest_first(client, deployment):
    _, dep_id, auth = deployment
    for _ in range(2):
        _finish(dep_id)
        assert client.post(f"/api/v1/deployments/{dep_id}/redeploy", headers=auth).status_code == 202

    resp = client.get(f"/api/v1/deployments/{dep_id}/jobs", headers=auth)
    assert resp.status_code == 200, resp.text
    runs = resp.json()
    assert len(runs) == 3  # the create plus two redeploys
    assert [r["status"] for r in runs] == ["queued", "succeeded", "succeeded"]
    assert resp.headers["X-Total-Count"] == "3"


def test_history_omits_the_log(client, deployment):
    _, dep_id, auth = deployment
    resp = client.get(f"/api/v1/deployments/{dep_id}/jobs", headers=auth)
    assert resp.status_code == 200
    assert "log" not in resp.json()[0]


def test_history_of_another_teams_deployment_is_not_found(client, deployment, auth_headers):
    _, dep_id, _ = deployment
    stranger = auth_headers(email="stranger@example.com")
    resp = client.get(f"/api/v1/deployments/{dep_id}/jobs", headers=stranger)
    assert resp.status_code == 404


def test_history_of_unknown_deployment_is_not_found(client, auth_headers):
    auth = auth_headers(email="nobody@example.com")
    resp = client.get(f"/api/v1/deployments/{uuid.uuid4()}/jobs", headers=auth)
    assert resp.status_code == 404


def test_repository_refuses_an_unknown_deployment(session, deployment):
    user = session.scalars(sa.select(User).where(User.email == "history@example.com")).first()
    repo = JobRepository(session, Scope.from_session(session, user.id))
    with pytest.raises(NotFoundError):
        repo.list_for_deployment(uuid.uuid4())


# ---------------------------------------------------------------------------
# One deploy at a time
# ---------------------------------------------------------------------------


def test_redeploy_while_a_deploy_runs_is_rejected(client, deployment):
    _, dep_id, auth = deployment
    resp = client.post(f"/api/v1/deployments/{dep_id}/redeploy", headers=auth)
    assert resp.status_code == 409, resp.text
    assert "already queued" in resp.json()["detail"]

    # And the rejected call left no second run behind.
    runs = client.get(f"/api/v1/deployments/{dep_id}/jobs", headers=auth).json()
    assert len(runs) == 1


def test_rejected_redeploy_does_not_strand_the_deployment_status(client, deployment):
    _, dep_id, auth = deployment
    before = client.get(f"/api/v1/deployments/{dep_id}", headers=auth).json()["status"]
    client.post(f"/api/v1/deployments/{dep_id}/redeploy", headers=auth)
    after = client.get(f"/api/v1/deployments/{dep_id}", headers=auth).json()["status"]
    assert after == before


def test_redeploy_allowed_once_the_previous_run_finished(client, deployment):
    _, dep_id, auth = deployment
    _finish(dep_id, "failed")
    resp = client.post(f"/api/v1/deployments/{dep_id}/redeploy", headers=auth)
    assert resp.status_code == 202, resp.text


def test_the_database_refuses_a_second_active_deploy(session, deployment):
    """The API check can be raced; the partial unique index cannot."""
    from sqlalchemy.exc import IntegrityError

    _, dep_id, _ = deployment
    project_id = session.scalar(
        sa.text("SELECT project_id FROM deployments WHERE id = :id"),
        {"id": uuid.UUID(dep_id)},
    )
    session.add(
        Job(
            project_id=project_id,
            deployment_id=uuid.UUID(dep_id),
            type="deploy",
            status="running",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_queue_deploy_raises_rather_than_double_queueing(session, deployment):
    from controlplane.db import SessionLocal
    from controlplane.models import Deployment

    _, dep_id, _ = deployment
    with SessionLocal() as db:
        dep = db.get(Deployment, uuid.UUID(dep_id))
        project = db.get(Project, dep.project_id)
        with pytest.raises(tasks.DeployAlreadyRunning) as exc:
            tasks.queue_deploy(dep, project, project.owner_id)
    assert exc.value.job.status == "queued"


def test_a_finished_run_does_not_block_other_deployments(client, deployment):
    """The rule is per deployment, not per project."""
    project_id, dep_id, auth = deployment
    resp = client.post(
        f"/api/v1/projects/{project_id}/deployments",
        json={
            "service_name": "worker",
            "repo_url": "https://github.com/org/worker.git",
            "branch": "main",
            "port": 8080,
        },
        headers=auth,
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Pipeline graph
# ---------------------------------------------------------------------------


def _job_id(client, dep_id, auth) -> str:
    runs = client.get(f"/api/v1/deployments/{dep_id}/jobs", headers=auth).json()
    return runs[0]["id"]


def test_graph_returns_steps_as_nodes_and_chained_edges(client, deployment, session):
    from datetime import UTC, datetime, timedelta

    from controlplane.models import JobStep

    project_id, dep_id, auth = deployment
    job_id = _job_id(client, dep_id, auth)
    now = datetime.now(UTC)
    session.add_all(
        [
            JobStep(
                job_id=uuid.UUID(job_id), step_index=1, step_total=9, name="cloning repository",
                status="succeeded", started_at=now, finished_at=now + timedelta(seconds=12),
            ),
            JobStep(
                job_id=uuid.UUID(job_id), step_index=2, step_total=9, name="secret scan (gitleaks) + gate",
                status="running", started_at=now + timedelta(seconds=12),
            ),
        ]
    )
    session.commit()

    resp = client.get(f"/api/v1/jobs/{job_id}/graph", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "job"
    assert body["title"] == "api · deploy"
    # A "deploy" job always fills to its 9-step template; rows 1-2 are
    # authoritative, the rest come from the template as not-yet-started.
    labels = [n["label"] for n in body["nodes"]]
    assert labels[:2] == ["cloning repository", "secret scan (gitleaks) + gate"]
    assert len(body["nodes"]) == 9
    assert [n["status"] for n in body["nodes"][:2]] == ["succeeded", "running"]
    assert body["nodes"][0]["duration_s"] == 12.0
    assert body["nodes"][0]["depends_on"] == []
    assert body["nodes"][1]["depends_on"] == [body["nodes"][0]["id"]]
    assert body["nodes"][2]["depends_on"] == [body["nodes"][1]["id"]]


def test_graph_has_single_node_fallback_for_step_less_jobs(client, deployment):
    """scan/destroy/undeploy jobs have no steps — the graph degrades to the
    job itself instead of an empty canvas."""
    project_id, dep_id, auth = deployment
    job_id = _job_id(client, dep_id, auth)
    resp = client.get(f"/api/v1/jobs/{job_id}/graph", headers=auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # A freshly created "deploy" job has no JobStep rows yet and no [n/N] log
    # markers, so it falls all the way back to a single job-shaped node.
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["id"] == "job"
    assert body["nodes"][0]["status"] == "pending"
    assert body["nodes"][0]["depends_on"] == []


def test_graph_of_another_teams_job_is_not_found(client, deployment, auth_headers):
    project_id, dep_id, auth = deployment
    job_id = _job_id(client, dep_id, auth)
    stranger = auth_headers(email="stranger@example.com")
    resp = client.get(f"/api/v1/jobs/{job_id}/graph", headers=stranger)
    assert resp.status_code == 404


def test_graph_requires_auth(client, deployment):
    project_id, dep_id, auth = deployment
    job_id = _job_id(client, dep_id, auth)
    resp = client.get(f"/api/v1/jobs/{job_id}/graph")
    assert resp.status_code == 401


def test_graph_of_unknown_job_is_not_found(client, auth_headers):
    auth = auth_headers()
    resp = client.get(f"/api/v1/jobs/{uuid.uuid4()}/graph", headers=auth)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Platform CI graph endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def admin_headers(client, session):
    from controlplane.core.security import hash_password
    from controlplane.models import User

    admin = User(
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Sup3rSecret!"),
        role="admin",
    )
    session.add(admin)
    session.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": admin.email, "password": "Sup3rSecret!"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _fake_gh(monkeypatch, run_view_payload: dict):
    """Point platform_ops at a stable slug and stub _run with canned gh JSON."""
    from controlplane import platform_ops

    monkeypatch.setattr(platform_ops, "_repo_slug", lambda: "acme/stage")

    def _fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "gh":
            return {"ok": True, "stdout": json.dumps(run_view_payload), "stderr": "", "code": 0}
        return {"ok": True, "stdout": "origin https://github.com/acme/stage.git", "stderr": "", "code": 0}

    monkeypatch.setattr(platform_ops, "_run", _fake_run)


def test_ci_graph_endpoint_returns_dag_shape(client, admin_headers, monkeypatch):
    _fake_gh(
        monkeypatch,
        {
            "displayTitle": "ci: main",
            "jobs": [
                {"databaseId": "101", "name": "Discover services", "status": "completed", "conclusion": "success"},
                {"databaseId": "102", "name": "Lint", "status": "completed", "conclusion": "success"},
                {"databaseId": "103", "name": "Tests + Dependency Audit", "status": "completed", "conclusion": "failure"},
            ],
        },
    )
    resp = client.get("/api/v1/platform/live/ci/7/graph", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is False
    assert body["title"] == "ci: main"
    assert body["nodes"], "DAG must come from the workflow file"
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id["discover"]["status"] == "succeeded"
    assert by_id["test"]["status"] == "failed"
    assert "lint" in by_id["test"]["depends_on"]


def test_ci_graph_endpoint_matrix_rolls_up(client, admin_headers, monkeypatch):
    _fake_gh(
        monkeypatch,
        {
            "displayTitle": "ci: matrix",
            "jobs": [
                {"databaseId": "201", "name": "Tests + Dependency Audit (a)", "status": "completed", "conclusion": "success"},
                {"databaseId": "202", "name": "Tests + Dependency Audit (b)", "status": "completed", "conclusion": "failure"},
            ],
        },
    )
    resp = client.get("/api/v1/platform/live/ci/8/graph", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    test_node = next(n for n in resp.json()["nodes"] if n["id"] == "test")
    assert test_node["status"] == "failed"
    assert test_node["detail"] == "2 matrix legs"


def test_ci_graph_endpoint_degraded_keeps_shape(client, admin_headers, monkeypatch):
    from controlplane import platform_ops

    monkeypatch.setattr(platform_ops, "_repo_slug", lambda: "acme/stage")
    monkeypatch.setattr(
        platform_ops, "_run",
        lambda cmd, **kw: {"ok": False, "stdout": "", "stderr": "gh not installed", "code": -1},
    )
    resp = client.get("/api/v1/platform/live/ci/9/graph", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is True
    assert "gh not installed" in body["degraded_reason"]
    assert body["nodes"], "degraded path must keep the DAG shape"
    assert all(n["status"] == "pending" for n in body["nodes"])
    by_id = {n["id"]: n for n in body["nodes"]}
    assert "lint" in by_id["test"]["depends_on"]
