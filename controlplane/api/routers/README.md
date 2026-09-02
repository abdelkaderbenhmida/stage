# api/routers

HTTP route handlers, one module per resource. Every handler goes through a repository
(`controlplane/repositories/`) for tenancy — never a direct model query — so the
"inaccessible resource → 404, not 403" rule holds automatically. Write endpoints that
kick off long-running work (provision, deploy, scan) only queue a Celery job and return
202; the job/log endpoints in `jobs.py` are how the caller follows progress.

- `auth.py` — register/login/refresh/logout, `/me`, and OIDC login/callback.
- `projects.py` — project CRUD, including TTL `extend`.
- `teams.py` — team CRUD, membership, per-team costs, git credential storage.
- `deployments.py` — deployment CRUD, CI status, workloads, quota, Tekton status,
  per-deployment secrets.
- `infrastructure.py` — provision/destroy/plan/nodes; all actual work runs in Celery,
  the request only enqueues it.
- `jobs.py` — job status, live log streaming (SSE), cancellation, pipeline graph.
- `scans.py` — trigger scans, list scans/findings, security summary.
- `catalogue.py` — cross-team "what's running, who owns it, is it safe" view, plus
  `/my/apps`.
- `logs.py` — per-project pod log tail, backed by Loki.
- `monitoring.py` — per-project CPU/memory view, backed by Prometheus.
- `platform.py` — the platform-ops console API (repo introspection, live cluster/CI/vault
  control); gated on the global operator role, not per-team RBAC.
- `webhooks.py` — the one unauthenticated, internet-facing endpoint: git provider push
  webhooks.
