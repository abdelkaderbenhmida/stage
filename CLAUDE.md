# Project Rules for Pipeline Graph Implementation

## Context
This project adds a pipeline graph visualization to the controlplane platform:
- Tenant deployment graph: 7 steps (clone→build→push→scan→render→rollout→URL) from `job_steps` table
- Platform CI graph: 13 GitHub Actions jobs from `parse_ci()` + `gh run view`

One shared renderer (`pipeline-graph.js`) consumes a normalized contract from two producers.

---

## Key Files to Modify

| File | Purpose |
|------|---------|
| `controlplane/models/job_step.py` | New model |
| `controlplane/migrations/versions/0009_job_steps.py` | Alembic migration (down_revision="0008") |
| `controlplane/workers/tasks.py` | `_step()` helper, 13 marker replacements, `_mark_job` closes step |
| `controlplane/api/schemas.py` | `GraphNode`, `GraphEdge`, `PipelineGraphOut` |
| `controlplane/api/routers/jobs.py` | Tenant graph endpoint |
| `controlplane/api/routers/platform.py` | CI graph endpoint (admin) |
| `controlplane/platform_ops.py` | `ci_run_graph(run_id)` |
| `controlplane/web/static/pipeline-graph.js` | Shared renderer (layout, svg, render) |
| `controlplane/web/static/shell.css` | `.pipe-*` styles |
| `controlplane/web/static/index.html` | Script load order |
| `controlplane/web/static/app.js` | `renderJob` integration |
| `controlplane/web/static/platform/app.js` | `renderCiTab` integration |

---

## Constraints & Conventions

1. **Alembic head is 0008** — migration must be 0009 with `down_revision = "0008"`

2. **SessionLocal has `expire_on_commit=False`** — `_step()` must open its own `SessionLocal()` and commit immediately (like `_append_log`). Never call inside another transaction.

3. **Status vocabulary: exactly 6 values** — queued, running, succeeded, failed, cancelled, skipped. Map all inputs:
   - Job.status → normalized (interrupted → failed)
   - GitHub status/conclusion → normalized (failure/timed_out/startup_failure → failed)

4. **CSP: script-src 'self'** — no inline handlers in HTML. Bind with `el.onclick = fn` (control-plane) or `data-act` dispatch (platform).

5. **Test UI parsing** — `tests/test_ui.py:53-60` regexes `platform/app.js` for `state.configTab`. Do not rename tabs or restructure that literal.

6. **Log format unchanged** — `_step()` still calls `_append_log(job_id, "[n/N] name")` so `parseStages`/`stageTracker` keep working as fallback.

7. **No bundler** — static files are ES modules/IIFEs loaded via `<script>`. `pipeline-graph.js` must be an IIFE exporting `window.PipelineGraph = { layout, svg, render }`.

8. **CSS scoping** — `shell.css` is the only global stylesheet. Prefix all new classes `.pipe-`. Do not touch leaked block in `style.css:279-333`.

9. **Layout algorithm** — Kahn topological → layer = longest path from roots. Within layer: barycentre (mean predecessor row), tie-break by id. Cycle guard: leftover nodes after Kahn → final layer with `detail: "dependency cycle"`. Fixed box: w=180, h=56, gapX=72, gapY=20.

10. **Matrix collapse** — one node per workflow job. GitHub matrix legs named `build (users-service)` → collapse by prefix match. Roll: any failure→failed, else any in_progress→running, else all success→succeeded.

11. **Degraded CI path** — when `gh` unreachable: return `reachable: false`, `error: "..."`, but still return nodes+edges with all statuses `skipped`. Console shows `offlineCard(...)`.

---

## Verification Checklist

- [ ] `cd controlplane && alembic upgrade head` creates `job_steps` table
- [ ] `alembic downgrade -1 && alembic upgrade head` clean
- [ ] `pytest controlplane/tests/test_pipeline_graph.py -q` passes (new unit tests)
- [ ] `pytest controlplane/tests/test_tasks.py -q` passes (job_steps assertions)
- [ ] `pytest controlplane/tests/test_deployment_jobs.py -q` passes (endpoint tests)
- [ ] `pytest controlplane/tests tests/ -q` full suite green (307 baseline)
- [ ] E2E: deploy service → job page shows live graph; CI tab shows DAG; offline CI shows greyed shape

---

## Agent Communication

Agents write to `.worktrees/.scratchpad/{owner}.md`:
```markdown
# {OWNER} Status
## Current Task    (ID + description)
## Progress        ([x]/[ ] steps)
## Needs from others
## Completed artifacts (path — description)
```

Read sibling scratchpads + `blockers.md` before starting.