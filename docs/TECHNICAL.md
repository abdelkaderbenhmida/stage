# Technical Specification — Pipeline Graph

## Tech Stack

| Layer | Technology | Rationale |
|-------|------------|-----------|
| Backend | FastAPI + SQLAlchemy 2.0 (async) | Existing controlplane stack; async endpoints for SSE + gh polling |
| Database | PostgreSQL (testcontainers in CI) | Existing; new `job_steps` table with FK cascade |
| Migrations | Alembic | Existing workflow; head at 0008 |
| Workers | Celery + sandboxed runners | Existing; `_step()` runs in deploy_task/provision_task sessions |
| Frontend (control-plane) | Vanilla ES modules, no bundler | Existing; IIFE globals, SSE, CSP 'self' |
| Frontend (platform) | Vanilla ES modules, IIFE + data-act dispatch | Existing; different global scope |
| CI Integration | GitHub CLI (`gh run view --json jobs`) | Existing `_run()` helper, 25s timeout |
| Graph Layout | Pure JS (Kahn + barycentre) | No deps, testable, ~150 lines |
| Rendering | Inline SVG strings | Matches existing sparkline pattern |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        BROWSER                                  │
│  ┌──────────────────┐     ┌──────────────────────────────────┐ │
│  │  control-plane   │     │         platform (ops)           │ │
│  │  #cp-root        │     │         #platform-root           │ │
│  │  renderJob()     │     │         renderCiTab()            │ │
│  │       │          │     │               │                  │ │
│  │       ▼          │     │               ▼                  │ │
│  │  PipelineGraph.render() ◄── shared renderer (pipeline-graph.js) │ │
│  └───────┬───────────┘     └───────────────┬─────────────────┘ │
│          │                                  │                  │
│          ▼                                  ▼                  │
│  ┌──────────────────┐     ┌──────────────────────────────────┐ │
│  │ GET /projects/   │     │ GET /platform/ci/runs/{id}/graph │ │
│  │ {pid}/jobs/      │     │ (admin only)                     │ │
│  │ {jid}/graph      │     │                                  │ │
│  └────────┬─────────┘     └──────────────┬───────────────────┘ │
└───────────┼──────────────────────────────┼──────────────────────┘
            │                              │
            ▼                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      BACKEND (FastAPI)                          │
│  ┌────────────────────┐  ┌──────────────────────────────────┐  │
│  │  routers/jobs.py   │  │  routers/platform.py             │  │
│  │  graph_endpoint()  │  │  ci_graph_endpoint()             │  │
│  └────────┬───────────┘  └───────────────┬───────────────────┘  │
│           │                               │                     │
│           ▼                               ▼                     │
│  ┌────────────────────┐  ┌──────────────────────────────────┐  │
│  │  repositories/     │  │  platform_ops.py                 │  │
│  │  JobRepository     │  │  ci_run_graph(run_id)            │  │
│  │  .get(job_id)      │  │    - parse_ci() → jobs, edges    │  │
│  └────────┬───────────┘  │    - _run(gh run view ...)       │  │
│           │              │    - collapse matrix, map status │  │
│           ▼              └───────────────┬───────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  models: Job, JobStep (new), Deployment, Project, ...    │  │
│  │  db: SessionLocal (expire_on_commit=False)               │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      WORKERS (Celery)                           │
│  tasks.py: _step() → JobStep row (own SessionLocal, commit)    │
│  _mark_job() → closes final step                               │
│  deploy_task / provision_task emit 13 step markers              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Models

### JobStep (new)

```python
class JobStep(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint("job_id", "index", name="uq_job_steps_job_index"),
        Index("ix_job_steps_job_id", "job_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(nullable=False)  # 1-based, sequential
    total: Mapped[int] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[str | None] = mapped_column(Text)
```

### Existing models (reference)

- `Job`: id, project_id, deployment_id, type, status, cancel_requested, started_at, finished_at, error_message
- `Deployment`: id, project_id, service_name, repo_url, branch, port, replicas, strategy, status, image_ref, live_url
- `Project`: id, owner_id, team_id, name, status, infra_spec, expires_at, auto_destroy, expiry_warned

---

## API Design

### Tenant Graph

```
GET /api/v1/projects/{project_id}/jobs/{job_id}/graph
Headers: Authorization: Bearer <token>
Response 200: PipelineGraphOut
Response 401: Unauthorized
Response 404: NotFoundError (job in other team → 404, not 403)
```

### CI Graph (platform admin)

```
GET /api/v1/platform/ci/runs/{run_id}/graph
Headers: Authorization: Bearer <token> (require_platform_admin)
Response 200: PipelineGraphOut (reachable: true|false in meta? — see schema)
Response 401: Unauthorized
Response 403: Forbidden (not platform admin)
```

### PipelineGraphOut (normalized contract)

```python
class GraphNode(BaseModel):
    id: str              # [a-z0-9_-]+, unique within graph
    name: str            # display label
    status: Literal["queued","running","succeeded","failed","cancelled","skipped"]
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    detail: str = ""
    url: str | None = None

class GraphEdge(BaseModel):
    from_: str = Field(alias="from")
    to: str

class PipelineGraphOut(BaseModel):
    source: Literal["deployment", "ci"]
    title: str
    status: Literal["queued","running","succeeded","failed","cancelled","skipped"]
    updated_at: datetime
    nodes: list[GraphNode]
    edges: list[GraphEdge]
```

---

## File Structure (proposed changes)

```
controlplane/
├── models/
│   ├── __init__.py                    # + export JobStep
│   ├── job_step.py                    # NEW
│   └── job.py                         # unchanged
├── migrations/versions/
│   └── 0009_job_steps.py              # NEW, down_revision="0008"
├── workers/
│   └── tasks.py                       # + _step(); replace 13 markers; _mark_job closes step
├── api/
│   ├── schemas.py                     # + GraphNode, GraphEdge, PipelineGraphOut
│   ├── routers/
│   │   ├── jobs.py                    # + GET /projects/{pid}/jobs/{jid}/graph
│   │   └── platform.py                # + GET /platform/ci/runs/{run_id}/graph
│   └── deps.py                        # unchanged
├── platform_ops.py                    # + ci_run_graph(run_id)
├── web/static/
│   ├── index.html                     # + <script src="pipeline-graph.js"> before app.js
│   ├── shell.css                      # + .pipe-* rules
│   ├── pipeline-graph.js              # NEW: layout, svg, render (IIFE)
│   ├── app.js                         # renderJob → PipelineGraph.render()
│   └── platform/
│       └── app.js                     # renderCiTab → per-run graph + poll timer
├── tests/
│   ├── test_pipeline_graph.py         # NEW: layout, status mapping, cycle, matrix
│   ├── test_tasks.py                  # + job_steps assertions
│   └── test_deployment_jobs.py        # + graph endpoint tests
└── docs/
    ├── PRD.md                         # (this spec)
    ├── TECHNICAL.md                   # (this file)
    └── TASKS.md                       # generated task list
```

---

## Implementation Phases (ordered, with deps)

### Wave 1 — Foundation (parallel)
| Task | Category | Deps | Description |
|------|----------|------|-------------|
| T1 | SETUP | — | Create `docs/`, write PRD/TECHNICAL/TASKS, scaffold CLAUDE.md |
| T2 | BACKEND | T1 | `models/job_step.py` + `models/__init__.py` export |
| T3 | BACKEND | T1 | Migration `0009_job_steps.py` (down_revision="0008") + `alembic upgrade head` verify |
| T4 | BACKEND | T1 | `api/schemas.py` — GraphNode, GraphEdge, PipelineGraphOut |
| T5 | BACKEND | T1 | `platform_ops.py` — `ci_run_graph(run_id)` reusing `parse_ci()` + `_run(gh...)` |

### Wave 2 — Workers + Tenant Endpoint (parallel)
| Task | Category | Deps | Description |
|------|----------|------|-------------|
| T6 | BACKEND | T2,T3 | `tasks.py`: `_step()` helper (own SessionLocal, commit), replace 13 `_append_log("[n/N]")` calls, `_mark_job` closes final step |
| T7 | BACKEND | T4 | `routers/jobs.py` — `GET /projects/{pid}/jobs/{jid}/graph` builds nodes from job_steps, edges chain |
| T8 | QA | T6 | Extend `test_tasks.py`: 7 job_steps with timestamps, failure→detail, truncation keeps 7 rows |

### Wave 3 — CI Endpoint + Renderer Core (parallel)
| Task | Category | Deps | Description |
|------|----------|------|-------------|
| T9 | BACKEND | T5 | `routers/platform.py` — `GET /platform/ci/runs/{run_id}/graph` with degraded path |
| T10 | FRONTEND | — | `web/static/pipeline-graph.js` — layout (Kahn+barycentre+cycle), svg, render |
| T11 | FRONTEND | — | `web/static/shell.css` — `.pipe-*` rules only |
| T12 | FRONTEND | — | `web/static/index.html` — load pipeline-graph.js first |

### Wave 4 — Frontend Integration (parallel)
| Task | Category | Deps | Description |
|------|----------|------|-------------|
| T13 | FRONTEND | T10,T11,T12 | `app.js` — `renderJob` calls `PipelineGraph.render()`, keeps stageTracker fallback, re-fetches on SSE log (throttle 2s) |
| T14 | FRONTEND | T10,T11,T12 | `platform/app.js` — `renderCiTab` per-run expander + 5s poll timer (state.pipelineTimer pattern) |

### Wave 5 — Tests + Verification (parallel)
| Task | Category | Deps | Description |
|------|----------|------|-------------|
| T15 | QA | T10 | `test_pipeline_graph.py` — status mapping, CI DAG layering, cycle guard, matrix collapse |
| T16 | QA | T7 | `test_deployment_jobs.py` — graph endpoint 200/404/401, CI endpoint monkeypatch `_run` |
| T17 | INTEGRATION | T6,T9,T13,T14 | `alembic upgrade head`; full pytest suite green; E2E browser smoke |

---

## Wave Dependency Graph

```
T1
 ├─ T2 ── T6 ── T8 ──┐
 ├─ T3 ──────────────┤
 ├─ T4 ── T7 ── T16 ─┤
 ├─ T5 ── T9 ────────┤
 ├─ T10 ─────────────┤── T13 ──┐
 ├─ T11 ─────────────┤         ├── T17
 └─ T12 ─────────────┤── T14 ──┘
        T15 ──────────┘
```