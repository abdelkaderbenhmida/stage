# controlplane

The control-plane service: a FastAPI API, a Celery worker, and the pieces both share
(config, database, models, business logic). It provisions and deploys tenant
environments in two modes — dedicated VMs (Terraform + Ansible) or a namespace carved
out of a shared cluster — and gates every rollout on a security scan. See the repo
root `README.md` for the API surface and operational scripts; this tree is the
implementation.

- `db.py` — SQLAlchemy engine/session setup. `SessionLocal` is `expire_on_commit=False`;
  `configure_database()` reconfigures the existing sessionmaker in place because
  `workers/tasks.py` binds `SessionLocal` at import time.
- `platform_ops.py` — the operator/platform console's backend logic (repo introspection,
  live cluster/CI/vault control), ported from a standalone `ui/` app.
- `alembic.ini` — migration config; `script_location` resolves relative to this file, so
  `alembic` commands must run with `cwd=controlplane/`.
- `Dockerfile` — image for both the API and the worker (same image, different entrypoint).
- `pyproject.toml` — ruff config (line-length 110, py312).
- `api/`, `core/`, `models/`, `parsers/`, `renderers/`, `repositories/`, `runners/`,
  `schemas/`, `workers/` — the application layers (see their own READMEs).
- `migrations/` — Alembic migration environment and revisions.
- `tests/` — the default test gate (`pytest controlplane/tests tests/`).
- `web/` — the buildless single-page UI served by the API.
