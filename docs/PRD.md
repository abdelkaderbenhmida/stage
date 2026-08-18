# Pipeline Graph — PRD

## Project Overview

**Problem**: The platform runs two pipelines (tenant deployment + platform CI) and shows neither as a graph. Tenant deployments run 7 steps in `deploy_task` but the console shows a flat list parsed by regex from log text. The platform's own CI is a real dependency graph of 13 GitHub Actions jobs — `parse_ci()` already extracts jobs and edges — but the console discards edges and renders only a flat list of runs.

**Solution**: A single reusable pipeline graph component that renders both pipelines as nodes joined by edges, laid out in columns by dependency depth. Two data producers (tenant + CI) emit a normalized contract; one renderer consumes it.

**Success Metrics**:
- Tenant job page shows live-updating graph with 7 steps, durations, and correct status colors
- Operations → CI shows per-run expanders with the real dependency shape (discover → lint → test → build → deploy, gitleaks/terraform-validate on side branches)
- CI graph draws even when gh is unreachable (degraded path: shape intact, statuses skipped, reason shown)
- All existing tests pass; new unit tests cover layout, status mapping, cycle detection, matrix collapse

---

## User Stories

1. **Tenant deploys a service**: Opens the job page and sees a horizontal pipeline graph filling in live — each node shows step name, status badge, duration. On SSE log delta, the graph re-fetches (throttled 2 s) and updates in place.

2. **Platform admin reviews CI**: Opens Operations → CI tab, expands a run row, and sees the full GitHub Actions DAG with edges, layered left-to-right. Matrix jobs collapse to one node with summary in detail.

3. **Network to GitHub fails**: CI graph still renders the pipeline shape; nodes are grey (skipped); an inline card explains "GitHub unreachable — showing static topology".

4. **Long build doesn't lose early steps**: Because steps are recorded in a `job_steps` table (not parsed from the truncated log), all 7 tenant steps remain visible even when `_LOG_CAP = 200_000` drops the head of the log.

---

## Core Features (Prioritized)

| # | Feature | Description |
|---|---------|-------------|
| 1 | `job_steps` model + migration | New table: job_id FK, index, total, name, status, started_at, finished_at, detail. Unique(job_id,index). Index(job_id). |
| 2 | `_step()` helper in tasks.py | Opens step `index` (closes previous → succeeded), inserts new as running, still calls `_append_log` with `[n/N]` text for parseStages compat. |
| 3 | Replace 13 marker calls | Lines 208,211,220,240 (provision VM 4); 314,317 (namespace 2); 986,1006,1018,1031,1105,1118,1130 (deploy 7). |
| 4 | `_mark_job` closes final step | Uses job's outcome: succeeded→succeeded; else failed + error_message→detail. Steps never reached → skipped. |
| 5 | Normalized contract schema | `PipelineGraphOut` with source, title, status, updated_at, nodes[], edges[]. Status vocab: 6 values (queued, running, succeeded, failed, cancelled, skipped). |
| 6 | Tenant graph endpoint | `GET /api/v1/projects/{pid}/jobs/{jid}/graph` — builds nodes from job_steps by index; edges are chain steps[i]→steps[i+1]. No steps → single node from job. Scope = JobRepository.get(job_id) → 404 convention. |
| 7 | CI graph endpoint | `GET /api/v1/platform/ci/runs/{run_id}/graph` — reuses `parse_ci()` for edges, `_run(["gh","run","view"...])` for statuses. Collapse matrix legs by name prefix. Degraded path: reachable:false + shape with all skipped. |
| 8 | `ci_run_graph(run_id)` in platform_ops | Join workflow jobs ↔ GitHub jobs on name (fallback id). Roll matrix: any failure→failed, else any running→running, else all success→succeeded. |
| 9 | Shared renderer: `pipeline-graph.js` | IIFE exporting `window.PipelineGraph = { layout, svg, render }`. Pure layout function (Kahn topological, barycentre within-layer, cycle guard). SVG string builder (inline, currentColor, role="img" + aria-label, visually-hidden <ul>). |
| 10 | CSS in shell.css | `.pipe-*` classes only. No leaked globals. Load order: shell.css → pipeline-graph.js → app.js / platform/app.js. |
| 11 | Frontend: `renderJob` uses graph | Replaces `${stageTracker(...)}` at app.js:1628. Keeps stageTracker as fallback. On SSE log event: re-fetch graph ≤once/2s, + once on done. No second stream. |
| 12 | Frontend: `renderCiTab` per-run graph | Each run row gains expander calling `PipelineGraph.render()`. Poll 5s while non-terminal (state.pipelineTimer pattern). Clear on switchView/unmount. |
| 12 | Unit tests | `test_pipeline_graph.py`: status mapping table, CI DAG layering (discover=0, test after lint+gitleaks, deploy last), cycle detection, matrix collapse. `test_tasks.py` extension: 7 job_steps rows with timestamps; failure carries error→detail; log truncation still has 7 rows. |
| 13 | Endpoint tests | `test_deployment_jobs.py` extension: graph returns nodes+edges; other-team 404; unauth 401. CI endpoint: monkeypatch `_run` for canned gh JSON, assert degraded path. |

---

## Constraints

- **Target repo**: `/home/gadour/Desktop/stage` (controlplane subpackage)
- **Alembic head**: 0008 (`deployment_service_unique.py`) — migration must chain as 0009 with `down_revision = "0008"`
- **Test suite**: 307 passing. `test_ui.py` regex-parses platform/app.js for `state.configTab` — do not rename tabs or restructure that literal.
- **CSP**: script-src 'self' — no inline handlers in markup. Bind with `el.onclick = fn` (control-plane) or data-act dispatch (platform).
- **Log format**: `_append_log` caps at 200 kB, keeps tail — do not change; _step must still emit `[n/N]` text.
- **Out of scope**: Expanding matrix legs; re-plumbing service_pipeline's 9 stages; changing log format or parseStages/stageTracker.