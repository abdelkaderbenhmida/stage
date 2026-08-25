"""Configuration reaches the container, and secrets do not reach anywhere else.

Before this the pod spec had no `env` at all, so the platform could only run
applications needing no configuration — every pod came up with
`Environment: <none>`.

The interesting assertions here are the negative ones: a secret value must not
appear on the deployment row, in any API response, or in the rendered
Deployment manifest. It belongs in a Secret object and nowhere else.
"""

import uuid

import pytest
from controlplane.core import app_config
from controlplane.db import SessionLocal
from controlplane.models import Project

SPEC = {
    "version": 1,
    "project": "cfg",
    "mode": "namespace",
    "network": {"cidr": "192.168.56.0/24", "domain": "demo.local"},
    "nodes": [{"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}],
}

SECRET_VALUE = "p0stgres://user:hunter2@db/app"


def _ready_project(client, auth, name="cfg"):
    resp = client.post(
        "/api/v1/projects",
        json={"name": name, "infra_spec": {**SPEC, "project": name}},
        headers=auth,
    )
    assert resp.status_code == 201, resp.text
    pid = resp.json()["id"]
    with SessionLocal() as db:
        db.get(Project, pid).status = "ready"
        db.commit()
    return pid


def _deploy(client, auth, pid, **extra):
    body = {
        "service_name": "web",
        "repo_url": "https://github.com/org/repo.git",
        "branch": "main",
        "port": 8080,
    }
    body.update(extra)
    return client.post(f"/api/v1/projects/{pid}/deployments", json=body, headers=auth)


@pytest.mark.integration
def test_environment_variables_are_stored_and_returned(auth_headers, client):
    auth = auth_headers("cfg-env@example.com")
    pid = _ready_project(client, auth)

    resp = _deploy(client, auth, pid, env={"LOG_LEVEL": "debug", "FEATURE_X": "1"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["env_vars"] == {"LOG_LEVEL": "debug", "FEATURE_X": "1"}


@pytest.mark.integration
def test_a_secret_value_never_appears_in_any_response(auth_headers, client):
    auth = auth_headers("cfg-secret@example.com")
    pid = _ready_project(client, auth)

    created = _deploy(client, auth, pid, secrets={"DATABASE_URL": SECRET_VALUE})
    assert created.status_code == 201, created.text

    # Only the name is reported back.
    assert created.json()["secret_keys"] == ["DATABASE_URL"]
    assert SECRET_VALUE not in created.text

    for resp in (
        client.get(f"/api/v1/projects/{pid}/deployments", headers=auth),
        client.get(f"/api/v1/deployments/{created.json()['id']}", headers=auth),
    ):
        assert SECRET_VALUE not in resp.text, "a secret value came back through the API"


@pytest.mark.integration
def test_a_secret_value_is_not_written_to_the_deployment_row(auth_headers, client):
    """The row is readable by anything that can see the deployment, and lands
    in every backup in plaintext."""
    auth = auth_headers("cfg-row@example.com")
    pid = _ready_project(client, auth)
    created = _deploy(client, auth, pid, secrets={"TOKEN": SECRET_VALUE})
    assert created.status_code == 201

    from controlplane.models import Deployment

    with SessionLocal() as db:
        row = db.get(Deployment, uuid.UUID(created.json()["id"]))
        serialised = repr({c.name: getattr(row, c.name) for c in row.__table__.columns})
    assert SECRET_VALUE not in serialised


@pytest.mark.integration
@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"env": {"9BAD": "x"}}, "name starts with a digit"),
        ({"env": {"has space": "x"}}, "name contains a space"),
        ({"env": {"PORT": "9999"}}, "PORT is set by the platform"),
        ({"env": {"A": "x"}, "secrets": {"A": "y"}}, "same name as both"),
    ],
)
def test_unusable_configuration_is_refused(auth_headers, client, payload, why):
    auth = auth_headers(f"cfg-bad-{abs(hash(why)) % 10000}@example.com")
    pid = _ready_project(client, auth)
    resp = _deploy(client, auth, pid, **payload)
    assert resp.status_code == 422, f"{why}: got {resp.status_code}"


# --- rendering --------------------------------------------------------------

def test_secret_values_are_not_rendered_into_the_deployment_manifest(tmp_path, monkeypatch):
    """They belong in a Secret, referenced by envFrom — not inlined."""
    from controlplane.workers import tasks

    team_id = uuid.uuid4()
    project_id = uuid.uuid4()
    deployment_id = uuid.uuid4()

    monkeypatch.setattr(app_config, "load_secrets", lambda *a: {"TOKEN": SECRET_VALUE})
    monkeypatch.setattr(tasks, "load_secrets", lambda *a: {"TOKEN": SECRET_VALUE})
    monkeypatch.setattr(tasks, "deployment_manifests_dir", lambda pid, mode, did: tmp_path)

    project = type("P", (), {
        "id": project_id, "name": "t", "team_id": team_id,
        "infra_spec": {"version": 1, "project": "t", "network": {}, "nodes": [], "mode": "namespace"},
    })()
    deployment = type("D", (), {
        "id": deployment_id, "service_name": "web",
        "repo_url": "https://github.com/o/r.git", "branch": "main",
        "port": 8080, "replicas": 1, "strategy": "deployment",
        "env_vars": {"LOG_LEVEL": "debug"}, "secret_keys": ["TOKEN"],
        "health_path": "/healthz",
    })()

    written = tasks._render_manifests(project, deployment, "registry/img:commit-abc")
    by_name = {p.name: p.read_text() for p in written}

    assert "secret.yaml" in by_name, "no Secret rendered for a deployment that has secrets"
    assert SECRET_VALUE in by_name["secret.yaml"]

    deployment_yaml = by_name["deployment.yaml"]
    assert SECRET_VALUE not in deployment_yaml, "secret inlined into the Deployment"
    assert "secretRef" in deployment_yaml, "Secret never referenced, so it would not reach the container"
    assert "LOG_LEVEL" in deployment_yaml
    # The probe path must follow the deployment, not stay hardcoded.
    assert "/healthz" in deployment_yaml
    assert "/livez" not in deployment_yaml


def test_the_rendered_secret_is_not_world_readable(tmp_path, monkeypatch):
    from controlplane.workers import tasks

    monkeypatch.setattr(tasks, "load_secrets", lambda *a: {"TOKEN": SECRET_VALUE})
    monkeypatch.setattr(tasks, "deployment_manifests_dir", lambda pid, mode, did: tmp_path)

    project = type("P", (), {
        "id": uuid.uuid4(), "name": "t", "team_id": uuid.uuid4(),
        "infra_spec": {"version": 1, "project": "t", "network": {}, "nodes": [], "mode": "namespace"},
    })()
    deployment = type("D", (), {
        "id": uuid.uuid4(), "service_name": "web", "repo_url": "https://github.com/o/r.git",
        "branch": "main", "port": 8080, "replicas": 1, "strategy": "deployment",
        "env_vars": {}, "secret_keys": ["TOKEN"], "health_path": "/livez",
    })()

    written = tasks._render_manifests(project, deployment, "registry/img:commit-abc")
    secret = next(p for p in written if p.name == "secret.yaml")
    assert secret.stat().st_mode & 0o077 == 0, "the rendered secret is readable by other users"
