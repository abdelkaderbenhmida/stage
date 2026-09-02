# tests

The control-plane test suite — half of the default gate (`pytest controlplane/tests
tests/`). `addopts` deselects the `integration`, `e2e`, and `network` markers by
default, so a plain green run never touched a database container; those markers run
separately with `-m integration` / `-m ""`.

- `conftest.py` — shared fixtures; sets required env vars before
  `controlplane.core.config` is imported (`WORKSPACE_ROOT` in particular, since the
  production default isn't writable by the test user). `pg_url`/`db_engine` spin a real
  PostgreSQL via testcontainers for integration tests.
- `fixtures/` — recorded scanner JSON reports used by the parser tests.
- One `test_*.py` per concern, named after what it covers rather than the module under
  test — e.g. `test_tasks.py` (worker tasks, the largest file), `test_e2e.py`,
  `test_tekton.py`, `test_argocd_gitops.py`, `test_teams_rbac.py`,
  `test_scan_gate_fails_closed.py`, `test_pipeline_graph.py`, `test_renderers.py`,
  `test_sandbox_secrets.py`, `test_oidc.py`. Grep for the behavior you're changing
  rather than assuming a 1:1 file mapping to `controlplane/`.
