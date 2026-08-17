"""Per-deployment pipeline history, and the one-deploy-at-a-time rule.

Every deploy was already recorded against its deployment, but nothing could
read those rows back, and nothing stopped a second deploy starting while the
first was still running — two builds of the same service pushing under the
same tag prefix and applying the same manifests, with the last one to finish
winning by accident.
"""

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
