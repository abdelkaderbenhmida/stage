"""Team isolation and role-based access control (docs/TODO.md Tasks 3.1, 3.2).

Covers the two rules that matter:
* a user in team A cannot see team B's projects (404, never 403), and a user
  in both teams sees both;
* the §3.2 role table is enforced on every write endpoint, and in the
  repository layer too (defence in depth).
"""

import uuid

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration

PROJECT_URL = "/api/v1/projects"


def _finish_deploy(deployment_id: str, status: str = "succeeded") -> None:
    """Terminate the deploy job in flight for a deployment."""
    from controlplane.db import SessionLocal
    from controlplane.workers import tasks

    with SessionLocal() as db:
        job = tasks.active_deploy_job(db, uuid.UUID(deployment_id))
        assert job is not None
        job.status = status
        db.commit()


def _register(client, email):
    password = "Str0ng!Passw0rd"
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "password_confirm": password},
    )
    assert resp.status_code == 201, resp.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _team_scene(client, auth_headers):
    """alice (admin) creates a team, then a project in it. bob=viewer,
    carol=developer, dave=owner. eve is not a member."""
    headers = {
        name: _register(client, f"{name}@example.com")
        for name in ("alice", "bob", "carol", "dave", "eve")
    }
    alice = headers["alice"]

    team = client.post("/api/v1/teams", json={"name": "Platform"}, headers=alice).json()
    tid = team["id"]
    for email, role in (
        ("bob@example.com", "viewer"),
        ("carol@example.com", "developer"),
        ("dave@example.com", "owner"),
    ):
        added = client.post(
            f"/api/v1/teams/{tid}/members",
            json={"email": email, "role": role},
            headers=alice,
        )
        assert added.status_code == 201, added.text

    created = client.post(
        PROJECT_URL,
        json={"name": "shared-app", "preset": "small", "team_id": tid},
        headers=alice,
    )
    assert created.status_code == 201, created.text
    return {
        "alice": alice,
        "bob": headers["bob"],
        "carol": headers["carol"],
        "dave": headers["dave"],
        "eve": headers["eve"],
        "team_id": tid,
        "project_id": created.json()["id"],
    }


# ---------------------------------------------------------------------------
# Isolation (Task 3.1)
# ---------------------------------------------------------------------------


def test_teammate_sees_team_project(client, auth_headers):
    scene = _team_scene(client, auth_headers)
    pid = scene["project_id"]

    got = client.get(f"{PROJECT_URL}/{pid}", headers=scene["bob"])
    assert got.status_code == 200
    assert got.json()["team_id"] == scene["team_id"]

    listing = client.get(PROJECT_URL, headers=scene["dave"])
    names = [p["name"] for p in listing.json()]
    assert "shared-app" in names


def test_non_member_sees_nothing_and_cannot_write(client, auth_headers):
    scene = _team_scene(client, auth_headers)
    pid = scene["project_id"]

    assert client.get(f"{PROJECT_URL}/{pid}", headers=scene["eve"]).status_code == 404
    assert client.get(f"{PROJECT_URL}/{pid}/nodes", headers=scene["eve"]).status_code == 404
    assert client.get(f"{PROJECT_URL}/{pid}/deployments", headers=scene["eve"]).status_code == 404
    assert client.post(f"{PROJECT_URL}/{pid}/scans", json={"tool": "trivy", "target": "https://github.com/example/repo.git"}, headers=scene["eve"]).status_code == 404
    assert client.post(f"{PROJECT_URL}/{pid}/provision", headers=scene["eve"]).status_code == 404
    assert client.get(PROJECT_URL, headers=scene["eve"]).json() == []


def test_member_of_two_teams_sees_both(client, auth_headers):
    scene = _team_scene(client, auth_headers)

    second = client.post("/api/v1/teams", json={"name": "Other"}, headers=scene["alice"]).json()
    second_id = second["id"]
    client.post(
        f"/api/v1/teams/{second_id}/members",
        json={"email": "bob@example.com", "role": "developer"},
        headers=scene["alice"],
    )
    created = client.post(
        PROJECT_URL,
        json={"name": "other-app", "preset": "small", "team_id": second_id},
        headers=scene["alice"],
    )
    assert created.status_code == 201, created.text

    names = [p["name"] for p in client.get(PROJECT_URL, headers=scene["bob"]).json()]
    assert set(names) == {"shared-app", "other-app"}
    assert client.get(f"{PROJECT_URL}/{created.json()['id']}", headers=scene["bob"]).status_code == 200


def test_cannot_create_project_in_unrelated_team(client, auth_headers):
    scene = _team_scene(client, auth_headers)
    resp = client.post(
        PROJECT_URL,
        json={"name": "sneaky", "preset": "small", "team_id": scene["team_id"]},
        headers=scene["eve"],
    )
    # Not a member: the team behaves as though it does not exist.
    assert resp.status_code == 404


def test_job_visibility_follows_team_membership(client, auth_headers, session):
    scene = _team_scene(client, auth_headers)
    pid = scene["project_id"]

    provision = client.post(f"{PROJECT_URL}/{pid}/provision", headers=scene["dave"])
    assert provision.status_code == 202, provision.text
    job_id = provision.json()["job_id"]

    assert client.get(f"/api/v1/jobs/{job_id}", headers=scene["dave"]).status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}", headers=scene["bob"]).status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}", headers=scene["eve"]).status_code == 404


# ---------------------------------------------------------------------------
# RBAC (Task 3.2) — one 403 test per cell of the §3.2 table
# ---------------------------------------------------------------------------


def _ready_project(client, auth_headers, scene):
    """Set the shared project to `ready` so deploy/scan endpoints accept it."""
    from controlplane.db import SessionLocal
    from controlplane.models import Project

    with SessionLocal() as db:
        project = db.get(Project, uuid.UUID(scene["project_id"]))
        project.status = "ready"
        db.commit()


def test_viewer_can_read_but_every_write_is_403(client, auth_headers):
    scene = _team_scene(client, auth_headers)
    pid = scene["project_id"]
    _ready_project(client, auth_headers, scene)

    assert client.get(f"{PROJECT_URL}/{pid}", headers=scene["bob"]).status_code == 200

    patch = client.patch(f"{PROJECT_URL}/{pid}", json={"description": "nope"}, headers=scene["bob"])
    assert patch.status_code == 403

    assert client.post(f"{PROJECT_URL}/{pid}/provision", headers=scene["bob"]).status_code == 403
    assert client.post(f"{PROJECT_URL}/{pid}/destroy", json={"confirm_name": "shared-app"}, headers=scene["bob"]).status_code == 403
    assert client.post(f"{PROJECT_URL}/{pid}/extend", json={"hours": 1}, headers=scene["bob"]).status_code == 403
    delete = client.request(
        "DELETE", f"{PROJECT_URL}/{pid}", json={"confirm_name": "shared-app"}, headers=scene["bob"]
    )
    assert delete.status_code == 403

    deploy = client.post(
        f"{PROJECT_URL}/{pid}/deployments",
        json={"service_name": "svc", "repo_url": "https://github.com/example/repo.git", "branch": "main", "port": 8080},
        headers=scene["bob"],
    )
    assert deploy.status_code == 403

    scan = client.post(f"{PROJECT_URL}/{pid}/scans", json={"tool": "trivy", "target": "https://github.com/example/repo.git"}, headers=scene["bob"])
    assert scan.status_code == 403


def test_developer_can_deploy_and_scan_but_not_provision(client, auth_headers):
    scene = _team_scene(client, auth_headers)
    pid = scene["project_id"]
    _ready_project(client, auth_headers, scene)

    assert client.post(f"{PROJECT_URL}/{pid}/provision", headers=scene["carol"]).status_code == 403
    assert client.post(f"{PROJECT_URL}/{pid}/destroy", json={"confirm_name": "shared-app"}, headers=scene["carol"]).status_code == 403

    deploy = client.post(
        f"{PROJECT_URL}/{pid}/deployments",
        json={"service_name": "svc", "repo_url": "https://github.com/example/repo.git", "branch": "main", "port": 8080},
        headers=scene["carol"],
    )
    assert deploy.status_code == 201, deploy.text

    scan = client.post(f"{PROJECT_URL}/{pid}/scans", json={"tool": "trivy", "target": "https://github.com/example/repo.git"}, headers=scene["carol"])
    assert scan.status_code == 202, scan.text

    # Creating the deployment already queued a deploy, and a deployment may
    # only have one in flight. This test is about carol's role, not about
    # concurrency, so let the first run finish as a worker would.
    _finish_deploy(deploy.json()["id"])

    redeploy = client.post(f"/api/v1/deployments/{deploy.json()['id']}/redeploy", headers=scene["carol"])
    assert redeploy.status_code == 202, redeploy.text

    delete = client.delete(f"/api/v1/deployments/{deploy.json()['id']}", headers=scene["carol"])
    assert delete.status_code == 200, delete.text


def test_owner_can_provision_extend_and_destroy(client, auth_headers, session):
    scene = _team_scene(client, auth_headers)
    pid = scene["project_id"]

    provision = client.post(f"{PROJECT_URL}/{pid}/provision", headers=scene["dave"])
    assert provision.status_code == 202, provision.text

    extend = client.post(f"{PROJECT_URL}/{pid}/extend", json={"hours": 1}, headers=scene["dave"])
    assert extend.status_code == 200, extend.text

    # The stub never runs the job, so it sits `queued` and would block the
    # destroy with a 409. Mark it terminal to simulate a finished provision.
    from controlplane.models import Job

    job = session.get(Job, uuid.UUID(provision.json()["job_id"]))
    job.status = "succeeded"
    session.commit()

    destroy = client.post(f"{PROJECT_URL}/{pid}/destroy", json={"confirm_name": "shared-app"}, headers=scene["dave"])
    assert destroy.status_code == 202, destroy.text


def test_only_admin_manages_members(client, auth_headers):
    scene = _team_scene(client, auth_headers)
    tid = scene["team_id"]

    for headers in (scene["bob"], scene["carol"], scene["dave"]):
        resp = client.post(
            f"/api/v1/teams/{tid}/members",
            json={"email": "eve@example.com", "role": "viewer"},
            headers=headers,
        )
        assert resp.status_code == 403

    ok = client.post(
        f"/api/v1/teams/{tid}/members",
        json={"email": "eve@example.com", "role": "viewer"},
        headers=scene["alice"],
    )
    assert ok.status_code == 201, ok.text


# ---------------------------------------------------------------------------
# Repository layer (defence in depth)
# ---------------------------------------------------------------------------


def test_repository_layer_404_for_non_member(client, auth_headers, session):
    scene = _team_scene(client, auth_headers)
    from controlplane.models import User
    from controlplane.repositories.base import NotFoundError, Scope
    from controlplane.repositories.projects import ProjectRepository

    eve = session.scalar(select(User).where(User.email == "eve@example.com"))
    scope = Scope.from_session(session, eve.id)
    repo = ProjectRepository(session, scope)

    with pytest.raises(NotFoundError):
        repo.get_project(uuid.UUID(scene["project_id"]))
    items, total = repo.list_projects()
    assert items == [] and total == 0


def test_repository_layer_forbids_viewer_write(client, auth_headers, session):
    scene = _team_scene(client, auth_headers)
    from controlplane.models import Project, User
    from controlplane.repositories.base import ForbiddenError, Scope
    from controlplane.repositories.projects import ProjectRepository

    bob = session.scalar(select(User).where(User.email == "bob@example.com"))
    scope = Scope.from_session(session, bob.id)
    repo = ProjectRepository(session, scope)
    project = session.get(Project, uuid.UUID(scene["project_id"]))

    # Visibility passes (member), but the viewer role cannot destroy -> 403.
    assert repo.get_project(project.id) is project
    with pytest.raises(ForbiddenError):
        repo.delete(project)
    with pytest.raises(ForbiddenError):
        repo.update_spec(project, project.infra_spec, "nope")

# ---------------------------------------------------------------------------
# Cost tracking (Task 5.1)
# ---------------------------------------------------------------------------


def test_team_costs_monthly_breakdown(client, auth_headers):
    from datetime import UTC, datetime, timedelta

    from controlplane.db import SessionLocal

    scene = _team_scene(client, auth_headers)
    headers = scene["alice"]

    # A second project in the same team, so the breakdown has two rows.
    created = client.post(
        PROJECT_URL,
        json={"name": "shared-billing", "preset": "small", "team_id": scene["team_id"]},
        headers=headers,
    )
    assert created.status_code == 201, created.text

    # Backdate the first project so its billable hours are non-trivial.
    from controlplane.models import Project as P

    with SessionLocal() as db:
        project = db.get(P, scene["project_id"])
        project.status = "ready"
        project.created_at = datetime.now(UTC) - timedelta(days=3)
        db.commit()

    resp = client.get(f"/api/v1/teams/{scene['team_id']}/costs", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["currency"] == "EUR"
    names = {row["project_name"] for row in body["projects"]}
    assert {"shared-app", "shared-billing"} <= names
    assert body["total"] == round(sum(row["total"] for row in body["projects"]), 2)
    assert body["total"] > 0


def test_team_costs_requires_membership(client, auth_headers):
    scene = _team_scene(client, auth_headers)
    resp = client.get(f"/api/v1/teams/{scene['team_id']}/costs", headers=scene["eve"])
    assert resp.status_code == 404
