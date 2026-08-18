# TaskList — Pipeline Graph

## Status legend
- `[ ]` todo
- `[~]` running
- `[x]` done
- `[!]` blocked

---

## Tasks

### Wave 1 — Foundation (parallel)

- [ ] T1 [SETUP] Write docs/PRD.md, TECHNICAL.md, TASKS.md; scaffold CLAUDE.md
- [ ] T2 [BACKEND] Create `controlplane/models/job_step.py` + export in `models/__init__.py`
- [ ] T3 [BACKEND] Create migration `controlplane/migrations/versions/0009_job_steps.py` (down_revision="0008"); run `alembic upgrade head` verify
- [ ] T4 [BACKEND] Add `GraphNode`, `GraphEdge`, `PipelineGraphOut` to `controlplane/api/schemas.py`
- [ ] T5 [BACKEND] Add `ci_run_graph(run_id)` to `controlplane/platform_ops.py` (reuse `parse_ci()` + `_run(["gh","run","view"...])`)

### Wave 2 — Workers + Tenant Endpoint (parallel)

- [ ] T6 [BACKEND] `controlplane/workers/tasks.py`: add `_step()` helper; replace 13 `_append_log("[n/N]")` calls; `_mark_job()` closes final step
- [ ] T7 [BACKEND] `controlplane/api/routers/jobs.py`: add `GET /projects/{pid}/jobs/{jid}/graph` (nodes from job_steps, edges chain)
- [ ] T8 [QA] Extend `controlplane/tests/test_tasks.py`: assert 7 job_steps rows with timestamps; failure carries error_message→detail; log truncation keeps 7 rows

### Wave 3 — CI Endpoint + Renderer Core (parallel)

- [ ] T9 [BACKEND] `controlplane/api/routers/platform.py`: add `GET /platform/ci/runs/{run_id}/graph` with degraded path (reachable:false, all skipped)
- [ ] T10 [FRONTEND] Create `controlplane/web/static/pipeline-graph.js` (IIFE: layout, svg, render)
- [ ] T11 [FRONTEND] Add `.pipe-*` rules to `controlplane/web/static/shell.css`
- [ ] T12 [FRONTEND] Edit `controlplane/web/static/index.html` to load `pipeline-graph.js` before `app.js`/`platform/app.js`

### Wave 4 — Frontend Integration (parallel)

- [ ] T13 [FRONTEND] `controlplane/web/static/app.js`: `renderJob` → `PipelineGraph.render()`; keep stageTracker fallback; SSE log event → re-fetch graph (throttle 2s) + once on done
- [ ] T14 [FRONTEND] `controlplane/web/static/platform/app.js`: `renderCiTab` per-run expander + 5s poll timer (state.pipelineTimer pattern; clear on switchView/unmount)

### Wave 5 — Tests + Verification (parallel)

- [ ] T15 [QA] Create `controlplane/tests/test_pipeline_graph.py`: status mapping table, CI DAG layering (discover=0, test after lint+gitleaks, deploy last), cycle guard, matrix collapse
- [ ] T16 [QA] Extend `controlplane/tests/test_deployment_jobs.py`: graph endpoint 200/404/401; CI endpoint monkeypatch `_run` for canned gh JSON, assert degraded path shape
- [ ] T17 [INTEGRATION] `cd controlplane && alembic upgrade head`; `pytest controlplane/tests tests/ -q` green; E2E browser smoke (deploy service → graph live; CI tab → graph matches DAG; offline → greyed shape)

---

## Wave Execution Order

```
Wave 1: T1, T2, T3, T4, T5          (parallel)
Wave 2: T6, T7, T8                  (parallel, after T2,T3,T4)
Wave 3: T9, T10, T11, T12           (parallel, after T4,T5)
Wave 4: T13, T14                    (parallel, after T10,T11,T12,T7,T9)
Wave 5: T15, T16, T17               (parallel, after T13,T14,T6,T9)
```