# api

The FastAPI application: entrypoint, shared request dependencies, auth/rate-limit
primitives, RBAC enforcement, and the Pydantic request/response schemas. Route handlers
themselves live in `api/routers/`.

- `main.py` — `create_app()` and the app `lifespan`; wires routers, middleware, CSP.
- `deps.py` — shared dependencies: DB session, `get_current_user` (JWT), `get_scope`
  (builds the `Scope` every repository requires), pagination headers, SSE stream tokens.
- `rbac.py` — `require_project_action` / `require_deployment_action` / `require_team_role`
  decorators. Enforces the invariant from the project root: an inaccessible resource
  raises `NotFoundError` → 404, never 403 (`ForbiddenError` → 403 is only used once a
  router has already proven the resource is visible). The operator console is instead
  gated on the *global* `User.role` via `require_platform_admin`, separate from
  per-team roles.
- `rate_limit.py` — in-memory sliding-window limiter; single-process only (v1).
- `metrics.py` — health metrics for AlertManager: Celery queue depth, beat liveness,
  stuck-job ratio — signals the request-latency instrumentator can't see on its own.
- `schemas.py` — Pydantic request/response models shared across routers (auth, projects,
  teams, git credentials, costs, catalogue, webhooks).
- `routers/` — one module per resource; see its own README.
