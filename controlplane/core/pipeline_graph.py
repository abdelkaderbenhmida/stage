"""Build the normalised pipeline-graph contract for a tenant job.

Pure DB-to-contract translation, no celery dependency, so both routers can
import it. Consumed by ``GET /jobs/{job_id}/graph``.

Sources, in order:
1. ``job_steps`` rows unioned with the declared template for the job type —
   rows are authoritative for label/status/timing, the template supplies the
   not-yet-started tail as pending nodes (or skipped once the job is terminal).
2. ``[n/N] name`` markers parsed out of the job log — the documented fallback
   for jobs that predate the table.
3. A single node built from the job itself (scan/destroy/undeploy emit no
   markers, so their graph is one box).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from controlplane.models import Deployment, Job, JobStep, Project
from controlplane.workers.steps import JOB_STEP_TEMPLATES

# Same regex the console uses (app.js parseStages), so both halves agree.
_LOG_STEP_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.+)$", re.MULTILINE)

_ID_OK = re.compile(r"[^A-Za-z0-9._:@-]+")

_TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")

# Job.status → graph vocabulary. interrupted maps to failed deliberately: a
# worker killed mid-run did not succeed, and the two terminal tuples in the
# codebase disagree about it; do not propagate that inconsistency.
JOB_GRAPH_STATUS: dict[str, str] = {
    "queued": "pending",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "interrupted": "failed",
}

_PROVISION_TEMPLATE_BY_TOTAL = {
    4: "provision:vm",
    2: "provision:namespace",
    1: "provision:pooled",
}


def _slugify(name: str, taken: set[str]) -> str:
    slug = _ID_OK.sub("-", name.strip().lower()).strip("-") or "step"
    base, i = slug, 2
    while slug in taken:
        slug = f"{base}-{i}"
        i += 1
    taken.add(slug)
    return slug


def _pick_template(job: Job, rows: list[JobStep], project: Project | None) -> list[str]:
    if job.type == "deploy":
        return JOB_STEP_TEMPLATES.get("deploy", [])
    if job.type == "provision":
        if rows:
            total = max((r.step_total for r in rows), default=0)
            key = _PROVISION_TEMPLATE_BY_TOTAL.get(total)
            if key:
                return JOB_STEP_TEMPLATES.get(key, [])
        mode = (project.infra_spec or {}).get("mode", "namespace") if project else "namespace"
        return JOB_STEP_TEMPLATES.get(f"provision:{mode}", [])
    return []


def _node_status(
    index: int, current: int, job_status: str, row: JobStep | None
) -> str:
    """Positional rule; a row carrying its own status always wins."""
    if row is not None and row.status != "running":
        return row.status
    if index < current:
        return "succeeded"
    if index > current:
        return "skipped" if job_status in _TERMINAL else "pending"
    if job_status in ("queued", "running"):
        return "running"
    if job_status == "succeeded":
        return "succeeded"
    if job_status == "cancelled":
        return "cancelled"
    return "failed"


def _duration_s(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if not started_at or not finished_at:
        return None
    delta = (finished_at - started_at).total_seconds()
    return max(0.0, delta)


def job_graph(db, job: Job) -> dict:
    """Build the contract dict for one job. Only 404s upstream (no external
    dependency, so no degraded path)."""
    project = db.get(Project, job.project_id) if job.project_id else None
    rows = (
        db.query(JobStep)
        .filter(JobStep.job_id == job.id)
        .order_by(JobStep.step_index)
        .all()
    )

    nodes = []
    if rows:
        by_index = {r.step_index: r for r in rows}
        template = _pick_template(job, rows, project)
        current = max(by_index)
        total = max(len(template), max(r.step_total for r in rows))
        taken: set[str] = set()
        for i in range(1, total + 1):
            row = by_index.get(i)
            if row is None and i > len(template):
                break
            label = row.name if row else template[i - 1]
            status = _node_status(i, current, job.status, row)
            detail = ""
            if row is not None and status == "failed" and row.error_message:
                detail = row.error_message[:200]
            nodes.append(
                {
                    "id": _slugify(label, taken),
                    "label": label,
                    "status": status,
                    "started_at": (row.started_at if row else None),
                    "finished_at": (row.finished_at if row else None),
                    "duration_s": (
                        _duration_s(row.started_at, row.finished_at) if row else None
                    ),
                    "detail": detail,
                    "depends_on": [nodes[-1]["id"]] if nodes else [],
                    "fanout": [],
                }
            )
    else:
        # Log fallback for jobs that predate the table.
        parsed = _parse_log_stages(job.log or "")
        if parsed:
            by_n = {n: (t, name) for n, t, name in parsed}
            total = max(t for _, t, _ in parsed)
            current = max(by_n)
            template = _pick_template(job, [], project)
            if job.type == "provision":
                key = _PROVISION_TEMPLATE_BY_TOTAL.get(total)
                template = JOB_STEP_TEMPLATES.get(key, []) if key else template
            taken = set()
            for i in range(1, total + 1):
                if i not in by_n and i > len(template):
                    break
                label = by_n[i][1] if i in by_n else template[i - 1]
                status = _node_status(i, current, job.status, None)
                nodes.append(
                    {
                        "id": _slugify(label, taken),
                        "label": label,
                        "status": status,
                        "started_at": None,
                        "finished_at": None,
                        "duration_s": None,
                        "detail": "",
                        "depends_on": [nodes[-1]["id"]] if nodes else [],
                        "fanout": [],
                    }
                )
        else:
            nodes = [_job_node(job, project)]

    return {
        "version": "pipeline-graph/1",
        "source": "job",
        "title": _job_title(db, job),
        "subtitle": f"{job.type} · {job.status}",
        "status": JOB_GRAPH_STATUS.get(job.status, "pending"),
        "url": None,
        "degraded": False,
        "degraded_reason": "",
        "detail": "worker interrupted" if job.status == "interrupted" else "",
        "generated_at": datetime.now(UTC),
        "nodes": nodes,
    }


def _job_title(db, job: Job) -> str:
    if job.deployment_id:
        deployment = db.get(Deployment, job.deployment_id)
        if deployment is not None and deployment.service_name:
            return f"{deployment.service_name} · {job.type}"
    return job.type


def _job_node(job: Job, project: Project | None) -> dict:
    return {
        "id": "job",
        "label": job.type,
        "status": JOB_GRAPH_STATUS.get(job.status, "pending"),
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "duration_s": _duration_s(job.started_at, job.finished_at),
        "detail": job.error_message or "",
        "depends_on": [],
        "fanout": [],
    }


def _parse_log_stages(log: str) -> list[tuple[int, int, str]]:
    """[(index, total, name)] in marker order, deduped by index."""
    out: dict[int, tuple[int, str]] = {}
    for m in _LOG_STEP_RE.finditer(log):
        n = int(m.group(1))
        out[n] = (int(m.group(2)), m.group(3).strip())
    return [(n, t, name) for n, (t, name) in sorted(out.items())]
