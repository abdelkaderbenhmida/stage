"""Shared fixtures. Integration tests spin up a real PostgreSQL via
testcontainers; everything else is pure unit."""

import os
import tempfile

import pytest

os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("JWT_SECRET", "dev-secret")
os.environ.setdefault("LOG_FORMAT", "plain")
os.environ.setdefault("VAULT_ADDR", "")
# Must be set before controlplane.core.config is imported: the production
# default (/var/lib/controlplane/workspaces) is not writable by the test user,
# and tasks that render manifests into the workspace would fail on it.
os.environ.setdefault(
    "WORKSPACE_ROOT", os.path.join(tempfile.gettempdir(), "controlplane-test-workspaces")
)


@pytest.fixture(scope="session")
def pg_url():
    """Start one PostgreSQL container for the whole session."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def db_engine(pg_url):
    """Configure controlplane's global engine + create the schema."""
    import sqlalchemy as sa
    from controlplane import db as dbmod
    from controlplane.models import Base

    dbmod.configure_database(pg_url)
    with dbmod.engine.connect() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.commit()
    Base.metadata.create_all(dbmod.engine)
    yield dbmod.engine


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The rate limiter (api/rate_limit.py) is a module-level singleton that
    persists for the whole test session. Without resetting it, login/register
    calls accumulate across unrelated tests and legitimately trip the 5/min
    cap well before any test intends to exercise rate limiting."""
    from controlplane.api.rate_limit import _limiter

    _limiter._events.clear()
    yield
    _limiter._events.clear()


class _InMemoryRedis:
    """Stand-in for redis.Redis covering the DevSecretStore surface.

    DevSecretStore talks to Redis because the API and worker are separate
    processes. Unit tests are one process and CI runs no Redis daemon, so the
    suite would otherwise fail on connection refused for every test that
    touches a secret. The store itself stays a real DevSecretStore, so the
    `isinstance(..., DevSecretStore)` checks in core/git_credentials.py still
    see what they see in production-without-Vault.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture(autouse=True)
def _in_memory_secret_store():
    """Give every test a fresh, hermetic dev secret store.

    Also isolates tests from each other: the store is a module-level
    singleton, so a token written by one test would otherwise still be
    readable by the next.
    """
    from controlplane.core import vault

    store = vault.DevSecretStore.__new__(vault.DevSecretStore)
    store._client = _InMemoryRedis()
    previous = vault._secret_store
    vault._secret_store = store
    yield store
    vault._secret_store = previous


@pytest.fixture(autouse=True)
def _clean_db(request):
    """Truncate all tables after every integration test, regardless of which
    fixtures it requested — without this, tests that use `client`/
    `auth_headers` but not `session` leak rows (e.g. the fixed
    alice@example.com user) into later tests and cause spurious 409s.

    Does not depend on `db_engine` directly so plain unit tests never trigger
    a testcontainers Postgres just by sharing this conftest."""
    yield
    if request.node.get_closest_marker("integration") is None:
        return
    from controlplane import db as dbmod

    _truncate_all(dbmod.engine)


from contextlib import contextmanager


@contextmanager
def override_settings(**changes):
    """Temporarily replace fields on the module-global Settings singleton.

    `Settings` is a frozen dataclass, so this reaches through `__dict__` (the
    same mechanism `object.__setattr__` uses) — but wrapped so every change is
    restored on exit and no test can leak settings into the next one. Prefer
    this over ad-hoc `object.__setattr__` in tests (docs/TODO.md §8 item 4).
    """
    from controlplane.core.config import settings

    snapshot = dict(settings.__dict__)
    for key, value in changes.items():
        if key not in snapshot:
            raise KeyError(f"Unknown setting {key!r}")
        object.__setattr__(settings, key, value)
    try:
        yield settings
    finally:
        for key, value in snapshot.items():
            object.__setattr__(settings, key, value)


@pytest.fixture()
def settings_override():
    """Fixture exposing `override_settings(**kwargs)` to tests."""
    return override_settings


@pytest.fixture()
def session(db_engine):
    """Per-test session for tests that need direct DB access."""
    from controlplane.db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _truncate_all(engine):
    import sqlalchemy as sa

    with engine.connect() as conn:
        conn.execute(sa.text("SET session_replication_role = replica"))
        conn.execute(
            sa.text(
                "TRUNCATE TABLE refresh_tokens, audit_log, findings, scans, jobs, nodes, "
                "deployments, projects, users, team_members, teams, webhook_subscriptions, "
                "pooled_clusters CASCADE"
            )
        )
        conn.execute(sa.text("SET session_replication_role = DEFAULT"))
        conn.commit()


@pytest.fixture()
def client(db_engine, monkeypatch):
    """FastAPI TestClient with only the Celery dispatch stubbed out.

    The queue_* functions in workers/tasks.py do real work beyond enqueuing
    (creating the Job row, setting project.workspace_path, returning the Job
    so the API can report job_id) — stubbing the whole function throws that
    away and breaks callers that depend on it. Only `.apply_async` actually
    needs to be faked, since that's what would try to reach Redis."""
    import controlplane.api.main as main
    import controlplane.workers.tasks as tasks
    from fastapi.testclient import TestClient

    class _FakeAsyncResult:
        id = "fake-task-id"

    def _fake_apply_async(*args, **kwargs):
        return _FakeAsyncResult()

    for task_name in ("provision_task", "destroy_task", "scan_task", "deploy_task", "undeploy_task"):
        monkeypatch.setattr(getattr(tasks, task_name), "apply_async", _fake_apply_async)

    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def auth_headers(client):
    """Register + login a fresh user, return Authorization header."""

    def _register(email="alice@example.com", password="Str0ng!Passw0rd"):
        resp = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "password_confirm": password},
        )
        assert resp.status_code == 201, resp.text
        login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return {"Authorization": f"Bearer {login.json()['access_token']}"}

    return _register
