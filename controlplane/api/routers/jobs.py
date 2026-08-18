"""Job status, live logs (SSE), and cancellation."""

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from controlplane.api.deps import (
    _stream_token,
    _validate_stream_token,
    audit,
    get_current_user,
    get_db,
    get_scope,
)
from controlplane.api.schemas import JobOut, Message, PipelineGraphOut, GraphNode, GraphEdge
from controlplane.db import SessionLocal
from controlplane.models import Job, User, JobStep
from controlplane.repositories.base import NotFoundError, Scope
from controlplane.repositories.jobs import JobRepository
from controlplane.workers.tasks import revoke_job

router = APIRouter(tags=["jobs"])


def _get(db: Session, scope: Scope, job_id: uuid.UUID) -> Job:
    try:
        return JobRepository(db, scope).get(job_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.") from None


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    return _get(db, scope, job_id)


@router.post("/jobs/{job_id}/stream-token", response_model=Message)
def issue_stream_token(
    job_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
):
    """Issue a short-lived stream token for streaming a job's logs.

    The browser EventSource API cannot set an Authorization header, so the
    log-stream endpoint accepts this token as a query parameter instead of
    the access token. The token is bound to this job only, expires in
    minutes, and is rejected by every endpoint that requires an access
    token.
    """
    job = _get(db, scope, job_id)
    token = _stream_token(job.id)
    audit(db, user.id, "jobs.issue_stream_token", request, "job", str(job.id))
    db.commit()
    return Message(message=token)


@router.get("/jobs/{job_id}/logs")
async def stream_logs(
    job_id: uuid.UUID,
    stream_token: str = Query(default=None),
    after: int = Query(default=0),
    db: Session = Depends(get_db),
):
    # Validate the short-lived, single-use stream token instead of using the
    # access token in a query string (which is commonly logged by proxies).
    if not stream_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Stream token required")
    job_id_from_token = _validate_stream_token(stream_token)
    if job_id_from_token != job_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    try:
        job = JobRepository(db, Scope.system()).get(job_id_from_token)
    except NotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from None

    async def events():
        sent = after
        terminal = False
        while not terminal:
            with SessionLocal() as session:
                current = session.get(Job, job.id)
                if current is None:
                    yield {"event": "done", "data": json.dumps({"reason": "job deleted"})}
                    return
                log = current.log or ""
                if len(log) > sent:
                    yield {
                        "event": "log",
                        "data": json.dumps({"delta": log[sent:]}),
                    }
                    sent = len(log)
                terminal = current.status in ("succeeded", "failed", "cancelled")
                if terminal:
                    yield {
                        "event": "done",
                        "data": json.dumps({"status": current.status}),
                    }
                    return
            await asyncio.sleep(1)

    return EventSourceResponse(events())


@router.post("/jobs/{job_id}/cancel", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
def cancel_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    job = _get(db, scope, job_id)
    if job.status not in ("queued", "running"):
        raise HTTPException(status_code=409, detail=f"Job is {job.status}; only queued/running jobs can be cancelled.")
    revoke_job(job)
    db.commit()
    return Message(message="Cancellation requested.")


@router.get("/projects/{project_id}/jobs/{job_id}/graph", response_model=PipelineGraphOut)
def job_graph(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    """Return a pipeline graph for the given job.

    Nodes are built from job_steps ordered by index; edges chain consecutive steps.
    If the job has no steps (scan, destroy, undeploy), a single node is returned
    from the job itself.
    """
    job = _get(db, scope, job_id)

    # Build nodes from job_steps
    steps = (
        db.query(JobStep)
        .filter(JobStep.job_id == job.id)
        .order_by(JobStep.index)
        .all()
    )

    if steps:
        nodes = []
        for step in steps:
            duration_s = None
            if step.started_at and step.finished_at:
                duration_s = (step.finished_at - step.started_at).total_seconds()
            nodes.append(
                GraphNode(
                    id=step.name.lower().replace(" ", "-"),
                    name=step.name,
                    status=step.status,
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                    duration_s=duration_s,
                    detail=step.detail or "",
                )
            )
        edges = [
            GraphEdge(from_=nodes[i].id, to=nodes[i + 1].id)
            for i in range(len(nodes) - 1)
        ]
    else:
        # Fallback for jobs without steps (scan, destroy, undeploy)
        duration_s = None
        if job.started_at and job.finished_at:
            duration_s = (job.finished_at - job.started_at).total_seconds()
        nodes = [
            GraphNode(
                id="job",
                name=job.deployment.service_name if job.deployment else job.type,
                status=job.status,
                started_at=job.started_at,
                finished_at=job.finished_at,
                duration_s=duration_s,
                detail=job.error_message or "",
            )
        ]
        edges = []

    updated_at = job.updated_at or job.finished_at or job.started_at or job.created_at
    return PipelineGraphOut(
        source="deployment",
        title=f"{job.project.name if job.project else 'unknown'} · {job.deployment.service_name if job.deployment else job.type}",
        status=job.status,
        updated_at=updated_at,
        nodes=nodes,
        edges=edges,
    )
