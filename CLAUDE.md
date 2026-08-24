# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`README.md` is the authoritative description of what the platform does, its API surface, config
variables and operational scripts. Read it for domain questions. This file covers what the README
does not: commands, and the invariants that are easy to break without noticing.

## Commands

```bash
# Full default suite (no cluster, no Docker required) — this is the gate
pytest controlplane/tests tests/

# One file / one test
pytest controlplane/tests/test_tasks.py -q
pytest controlplane/tests/test_tasks.py::test_name -x -q

# Integration tests spin real PostgreSQL via testcontainers (needs Docker)
pytest controlplane/tests -q -m integration
pytest controlplane/tests tests/ -m ""      # everything, including e2e/network

# Lint (config in controlplane/pyproject.toml: ruff, line-length 110, py312)
ruff check controlplane/ app/ tests/

# Migrations — alembic.ini resolves script_location relative to itself
(cd controlplane && alembic upgrade head)
(cd controlplane && alembic downgrade -1)

# Run locally (see README "Getting started" for the supporting services)
uvicorn controlplane.api.main:app --port 8000
celery -A controlplane.workers.celery_app worker --loglevel=info --concurrency=2
```

`addopts` deselects `integration`, `e2e` and `network` markers by default, so a green
`pytest` run has not touched a database container. CI runs `pytest -q tests/` and
`pytest -q controlplane/tests -m "not integration"` as two separate jobs — a change that
only passes when both suites run together will still fail CI.

## Architecture: the load-bearing parts

**Tenancy lives in `controlplane/repositories/`, not in routers.** Every repository
constructor takes an explicit `Scope` (`api/deps.py:get_scope`); there is no default scope
anywhere, so a forgotten filter is a type error rather than a leak. Inaccessible resources
raise `NotFoundError` → **404, never 403** — a 403 would confirm another team's resource
exists. `ForbiddenError` → 403 is only for the case where the router already proved
visibility. When adding an endpoint, go through a repository; do not query models directly.

**Namespaces derive from the project UUID**, never the project name (`core/validation.py`).
Two teams may both name a project `staging`.

**Three layers of the request path:** routers (`api/routers/`) → repositories (tenancy) →
models. RBAC is `api/rbac.py` + the action→role table in `core/roles.py`; the operator
console is gated on the *global* `User.role`, separate from per-team roles.

**Every external command goes through `runners/sandbox.py`.** Nothing shells out directly.
Secrets go into a 0600 env-file, never argv (`docker run -e K=V` is world-readable via
`/proc`). The Docker socket is mounted only for build and push.

**Workers write their own sessions.** `SessionLocal` is `expire_on_commit=False`. Helpers
that stream progress (`_append_log`, `_step` in `workers/tasks.py`) open their own
`SessionLocal()` and commit immediately — they must never be called inside another
transaction, or the job log stops updating until the outer commit.
`db.configure_database()` reconfigures the existing sessionmaker in place because
`workers/tasks.py` bound `SessionLocal` at import time.

**The scan gate fails closed.** An unreadable or failed Trivy report blocks the rollout
exactly like a CRITICAL finding. There is no bypass flag, and `.platform.yml` cannot add one.

**Two opt-in paths change the deploy pipeline.** Both default off, both namespace-mode
only (a VM-mode project's own cluster has neither ArgoCD nor Tekton in it):

- `GITOPS_ENABLED` — the worker commits rendered manifests to the platform manifest repo
  and creates an ArgoCD Application instead of `kubectl apply`. Isolation is the
  Application's derived `destination.namespace` **plus** the team's AppProject whitelist;
  the AppProject is the server-side half and is not optional. Rendered Secrets are never
  committed. `renderers/argocd.py`, `runners/gitops.py`, `k8s/gitops/`.
- `TEKTON_ENABLED` — clone/build/scan become Pods in the tenant's namespace, built with
  kaniko. A repository's own `.platform.yml` stages still run, each as its own Task in
  the submitted PipelineRun; Dockerfile autogeneration is what's lost, since it reads a
  host checkout this path never produces — a repo without a committed Dockerfile fails
  here. Needs `REGISTRY_CIDR` set to the registry's own subnet or every build times out
  reaching it (the tenant namespace's default-deny egress excludes RFC1918 by design).
  `runners/tekton.py`, `core/tekton_status.py`, `k8s/tekton/`.

`GITOPS_REPO_URL` vs `GITOPS_REPO_URL_INTERNAL` is the same host/in-cluster address split
as `REGISTRY` vs `REGISTRY_INTERNAL` — setting them equal breaks one side, and the
failure reads as "repository not found".

## Conventions that tests enforce

- **Job/step status vocabulary is exactly six values**: `queued, running, succeeded, failed,
  cancelled, skipped`. Anything external (GitHub conclusions, `interrupted` jobs) is
  normalized into that set before it reaches a schema.
- **CSP is `script-src 'self'`** — no inline handlers in HTML. Bind via `el.onclick = fn`
  (control-plane console) or `data-act` dispatch (platform console).
- **No bundler.** `controlplane/web/static/*.js` are plain scripts loaded in order by
  `index.html`; shared modules are IIFEs hanging one object off `window`.
- **`shell.css` is the global stylesheet**; `style.css:279-333` contains a known leaked block
  — leave it alone. Prefix pipeline-graph classes `.pipe-`.
- **`tests/test_ui.py` regexes `web/static/platform/app.js`** for literals such as
  `state.configTab`. Renaming console tabs or restructuring those literals breaks it.
- **Job log format `[n/N] name`** is parsed by the console (`parseStages`/`stageTracker`) as a
  fallback when the graph endpoint is unavailable. `_step()` must keep emitting it.
- **Tekton status is a condition, not a field.** `Succeeded` carries the string "True" /
  "False" / "Unknown"; "Unknown" means both running and not-yet-started, and a
  cancellation arrives as "False" exactly like a failure. `core/tekton_status.py` owns
  that mapping — do not re-derive it at a call site.
- **Task names in `k8s/tekton/pipeline.yaml` must match `TEKTON_PIPELINE_TASKS`** in
  `runners/tekton.py`; a test asserts it, because a rename on one side draws a graph with
  a permanently-pending box.
- **Migrations are sequential**: new revision = `NNNN_name.py` with `down_revision` pointing
  at the current head (currently `0010`). Both directions must run clean.

## Agent scratchpads

Parallel agents write status to `.worktrees/.scratchpad/{owner}.md` and read sibling
scratchpads plus `blockers.md` before starting.
