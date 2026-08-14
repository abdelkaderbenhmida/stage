"""Stream token (SSE) issuing and validation.

The browser EventSource API cannot set an Authorization header, so the
log-stream endpoint uses a short-lived, job-scoped stream token minted by
POST /jobs/{id}/stream-token instead of the access token (which would leak
into proxy logs). Unit tests cover the token primitives; the integration
tests cover the two endpoints.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from controlplane.api.deps import _stream_token, _validate_stream_token
from controlplane.core.config import settings
from controlplane.core.security import create_access_token, decode_access_token
from controlplane.models import Job

VALID_SPEC = {
    "version": 1,
    "project": "stream-proj",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "nodes": [
        {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
        {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
    ],
}


def _create_project(client, headers, name="stream-proj"):
    return client.post(
        "/api/v1/projects",
        json={"name": name, "infra_spec": VALID_SPEC},
        headers=headers,
    )


def _token(payload: dict) -> str:
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


# --- unit: token primitives -------------------------------------------------

def test_stream_token_roundtrip():
    job_id = uuid.uuid4()
    token = _stream_token(job_id)
    assert _validate_stream_token(token) == job_id


def test_stream_token_is_job_scoped():
    a, b = uuid.uuid4(), uuid.uuid4()
    token = _stream_token(a)
    assert _validate_stream_token(token) == a
    assert _validate_stream_token(token) != b


def test_stream_token_is_not_an_access_token():
    token = _stream_token(uuid.uuid4())
    assert decode_access_token(token) is None


def test_access_token_rejected_as_stream_token():
    with pytest.raises(Exception) as exc:
        _validate_stream_token(create_access_token(str(uuid.uuid4())))
    assert exc.typename == "HTTPException"


def test_expired_stream_token_rejected():
    token = _token({"sub": str(uuid.uuid4()), "type": "stream", "exp": datetime.now(UTC) - timedelta(minutes=1)})
    with pytest.raises(Exception) as exc:
        _validate_stream_token(token)
    assert exc.typename == "HTTPException"


def test_garbage_stream_token_rejected():
    with pytest.raises(Exception) as exc:
        _validate_stream_token("not-a-jwt")
    assert exc.typename == "HTTPException"


def test_stream_token_with_bad_subject_rejected():
    token = _token({"sub": "not-a-uuid", "type": "stream", "exp": datetime.now(UTC) + timedelta(minutes=5)})
    with pytest.raises(Exception) as exc:
        _validate_stream_token(token)
    assert exc.typename == "HTTPException"


# --- integration: endpoints -------------------------------------------------

@pytest.mark.integration
def test_stream_token_requires_auth(client):
    resp = client.post(f"/api/v1/jobs/{uuid.uuid4()}/stream-token")
    assert resp.status_code == 401


@pytest.mark.integration
def test_stream_token_requires_job_exists(client, auth_headers):
    headers = auth_headers()
    resp = client.post(f"/api/v1/jobs/{uuid.uuid4()}/stream-token", headers=headers)
    assert resp.status_code == 404


@pytest.mark.integration
def test_stream_token_then_logs(client, auth_headers, session):
    headers = auth_headers()
    project = _create_project(client, headers, "stream-proj").json()
    job = Job(project_id=project["id"], type="provision", status="succeeded", log="line one\nline two\n")
    session.add(job)
    session.commit()

    resp = client.post(f"/api/v1/jobs/{job.id}/stream-token", headers=headers)
    assert resp.status_code == 200
    stream_token = resp.json()["message"]

    with client.stream(
        "GET", f"/api/v1/jobs/{job.id}/logs?stream_token={stream_token}"
    ) as stream:
        body = "".join(stream.iter_text())
    assert "line one" in body and "line two" in body
    assert "event: done" in body


@pytest.mark.integration
def test_stream_token_rejected_on_wrong_job(client, auth_headers, session):
    headers = auth_headers()
    project = _create_project(client, headers, "stream-proj-2").json()
    a = Job(project_id=project["id"], type="provision", status="succeeded", log="a")
    b = Job(project_id=project["id"], type="provision", status="succeeded", log="b")
    session.add_all([a, b])
    session.commit()

    stream_token = client.post(f"/api/v1/jobs/{a.id}/stream-token", headers=headers).json()["message"]
    resp = client.get(f"/api/v1/jobs/{b.id}/logs?stream_token={stream_token}")
    assert resp.status_code == 404


@pytest.mark.integration
def test_logs_rejects_access_token_in_query(client, auth_headers, session):
    headers = auth_headers()
    project = _create_project(client, headers, "stream-proj-3").json()
    job = Job(project_id=project["id"], type="provision", status="succeeded", log="x")
    session.add(job)
    session.commit()

    access_token = headers["Authorization"].removeprefix("Bearer ")
    resp = client.get(f"/api/v1/jobs/{job.id}/logs?stream_token={access_token}")
    assert resp.status_code == 401