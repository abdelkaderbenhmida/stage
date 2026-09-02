# models

SQLAlchemy ORM models. Models are dumb data holders — tenancy filtering and access
control live in `controlplane/repositories/`, not here; routers and workers should not
query these directly.

- `base.py` — declarative base and shared column mixins (timestamps, UUID PK).
- `__init__.py` — imports every model so Alembic autogenerate and
  `metadata.create_all` can see them all; add new models here too.
- `user.py` — `User`, including the global `role` field used for operator-console RBAC.
- `team.py` — `Team` and membership; teams, not individuals, own projects.
- `project.py` — `Project` (VM-mode or namespace-mode environment).
- `deployment.py` — `Deployment` (a service within a project).
- `job.py` — `Job`, the async unit of work behind provision/deploy/scan; carries the
  six-value status vocabulary (`queued, running, succeeded, failed, cancelled, skipped`).
- `job_step.py` — one recorded pipeline step per job, written by
  `workers/tasks.py::_step()` at each stage boundary.
- `pool.py` — warm cluster pool entries.
- `scan.py` — security scan runs and their findings.
- `webhook.py` — git webhook subscriptions.
- `audit.py` — `AuditLog` and `RefreshToken`.
