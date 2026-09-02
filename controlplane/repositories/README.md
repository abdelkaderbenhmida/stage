# repositories

The tenancy enforcement layer. Every query that touches projects, nodes, deployments,
jobs, scans, or findings goes through here, scoped to the caller's team memberships
(with a fallback to direct ownership for projects created before teams existed).
Routers must not query models directly — always through a repository.

**Every repository constructor takes an explicit `Scope`** (built by `api/deps.py:
get_scope`); there is no default scope anywhere, so a forgotten filter is a type error,
not a silent leak. A resource the caller cannot see raises `NotFoundError` → 404, never
403, so a cross-tenant probe can't even confirm the resource exists.

- `base.py` — `Scope`, `NotFoundError`, `ForbiddenError`, and `paginate()`; the shared
  primitives every other repository builds on.
- `projects.py` — `ProjectRepository`.
- `deployments.py` — `DeploymentRepository`.
- `jobs.py` — `JobRepository`; notably guards against orphan jobs (no project, or a
  since-deleted project) being returned to any authenticated caller.
- `teams.py` — team membership queries.
- `users.py` — user lookups and refresh-token handling.
