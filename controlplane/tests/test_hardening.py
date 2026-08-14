"""docs/TODO.md §7 production-readiness items 3, 4, 5.

3. DB connection pool limits are configurable and applied to the engine.
4. Structured JSON logging with request ids (middleware + formatters).
5. Graceful shutdown: SIGTERM marks in-process running jobs interrupted.
"""

import json
import logging
import uuid

import pytest
from controlplane.core.logging import JsonFormatter, PlainFormatter, request_id_var
from sqlalchemy.pool import QueuePool

# --- item 3: DB pool limits -------------------------------------------------

def test_engine_pool_limits_applied(settings_override):
    from controlplane import db as dbmod

    with settings_override(db_pool_size=3, db_max_overflow=7, db_pool_recycle=60):
        engine = dbmod._engine("postgresql+psycopg://u:p@localhost:5432/db")
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == 3
    assert engine.pool._max_overflow == 7


def test_engine_sqlite_ignores_pool_limits(settings_override):
    from controlplane import db as dbmod

    with settings_override(db_pool_size=3, db_max_overflow=7):
        engine = dbmod._engine("sqlite:///:memory:")
    assert not isinstance(engine.pool, QueuePool)


# --- item 4: structured logging ----------------------------------------------

def _record(message="hello"):
    record = logging.LogRecord(
        name="controlplane.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=None,
    )
    return record


def test_json_formatter_emits_structured_line():
    line = JsonFormatter().format(_record())
    payload = json.loads(line)
    assert payload["logger"] == "controlplane.test"
    assert payload["level"] == "info"
    assert payload["msg"] == "hello"
    assert "ts" in payload and "request_id" not in payload


def test_json_formatter_includes_request_id_and_extra():
    token = request_id_var.set("req-abc123")
    try:
        record = _record()
        record.extra_fields = {"job_id": "j-1", "status": 200}
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert payload["request_id"] == "req-abc123"
    assert payload["job_id"] == "j-1"


def test_plain_formatter_includes_request_id():
    token = request_id_var.set("req-xyz")
    try:
        line = PlainFormatter().format(_record())
    finally:
        request_id_var.reset(token)
    assert "[req-xyz]" in line


def test_plain_formatter_omits_request_id_when_absent():
    # Task code sets request_id_var without resetting it, and celery worker
    # tests run tasks on the same thread as later tests — so do not rely on
    # the var being pristine; pin it to None for this assertion.
    token = request_id_var.set(None)
    try:
        line = PlainFormatter().format(_record())
    finally:
        request_id_var.reset(token)
    assert "[req-" not in line


# --- item 5: graceful shutdown ------------------------------------------------

def test_track_signals_track_job_ids():
    from controlplane.workers import celery_app as worker

    worker._running_jobs.clear()
    worker._track_started("t1", type("T", (), {"name": "controlplane.workers.tasks.provision_task"})(), ["job-1"])
    assert worker._running_jobs == {"job-1"}
    worker._track_started("t2", type("T", (), {"name": "controlplane.workers.tasks.reap_expired_projects"})(), [])
    assert worker._running_jobs == {"job-1"}
    worker._track_finished("t1", type("T", (), {"name": "controlplane.workers.tasks.provision_task"})(), ["job-1"])
    assert worker._running_jobs == set()


@pytest.mark.integration
def test_shutdown_marks_running_job_interrupted(client, session, auth_headers):
    from controlplane.models import Job
    from controlplane.workers import celery_app as worker

    headers = auth_headers()
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "shutdown-proj",
            "infra_spec": {
                "version": 1,
                "project": "shutdown-proj",
                "network": {"cidr": "192.168.56.0/24", "domain": "d.local"},
                "nodes": [
                    {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
                    {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
                ],
            },
        },
        headers=headers,
    ).json()

    running = Job(project_id=project["id"], type="provision", status="running")
    idle = Job(project_id=project["id"], type="provision", status="running")
    session.add_all([running, idle])
    session.commit()

    worker._running_jobs.clear()
    worker._track_started("t-a", type("T", (), {"name": "controlplane.workers.tasks.provision_task"})(), [str(running.id)])
    worker._mark_interrupted()

    session.refresh(running)
    session.refresh(idle)
    assert running.status == "interrupted"
    assert running.finished_at is not None
    assert "shutdown" in (running.error_message or "").lower()
    assert idle.status == "running"  # not touched: not in this worker
    worker._running_jobs.clear()


@pytest.mark.integration
def test_queue_stamps_request_id(client, auth_headers):
    """A job queued inside an HTTP request carries the request id."""
    from controlplane.models import Job

    headers = auth_headers()
    project = client.post(
        "/api/v1/projects",
        json={
            "name": "reqid-proj",
            "infra_spec": {
                "version": 1,
                "project": "reqid-proj",
                "network": {"cidr": "192.168.56.0/24", "domain": "d.local"},
                "nodes": [
                    {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
                ],
            },
        },
        headers=headers,
    ).json()

    resp = client.post(
        f"/api/v1/projects/{project['id']}/provision",
        headers={**headers, "X-Request-Id": "req-12345678"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.headers["X-Request-Id"] == "req-12345678"

    job_id = resp.json()["job_id"]
    from controlplane.db import SessionLocal

    with SessionLocal() as session:
        job = session.get(Job, uuid.UUID(job_id))
        assert job.request_id == "req-12345678"

# --- §7 item 2: secrets in Vault, fail closed --------------------------------

def test_config_secret_reads_none_in_dev():
    from controlplane.core.vault import read_config_secret

    assert read_config_secret("jwt_secret") is None


def test_require_secrets_dev_is_noop(settings_override):
    from controlplane.core.config import settings

    with settings_override(environment="dev", jwt_secret=""):
        settings.require_secrets()  # must not raise


def test_require_secrets_production_fails_closed(settings_override, monkeypatch):
    from controlplane.core.config import settings

    monkeypatch.delenv("JWT_SECRET", raising=False)
    with settings_override(environment="production", jwt_secret="", vault_addr="", vault_token=""):
        with pytest.raises(SystemExit) as exc:
            settings.require_secrets()
    assert "JWT_SECRET is unset" in str(exc.value)


def test_require_secrets_resolves_from_vault(settings_override, monkeypatch):
    from controlplane.core.config import settings

    monkeypatch.delenv("JWT_SECRET", raising=False)

    def _fake_read(key):
        assert key == "jwt_secret"
        return "vault-secret-abc"

    monkeypatch.setattr("controlplane.core.vault.read_config_secret", _fake_read)
    with settings_override(environment="production", jwt_secret=""):
        settings.require_secrets()
        assert settings.jwt_secret == "vault-secret-abc"


def test_require_secrets_vault_missing_still_fails(settings_override, monkeypatch):
    from controlplane.core.config import settings

    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr("controlplane.core.vault.read_config_secret", lambda key: None)
    with settings_override(environment="production", jwt_secret=""):
        with pytest.raises(SystemExit):
            settings.require_secrets()
