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
from controlplane.api.schemas import JobOut, Message, PipelineGraphOut
from controlplane.core.pipeline_graph import job_graph as build_job_graph
from controlplane.db import SessionLocal
from controlplane.models import Job, User
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


@router.get("/jobs/{job_id}/graph", response_model=PipelineGraphOut)
def job_graph(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    """Return a pipeline graph for the given job.

    Built by ``core.pipeline_graph.job_graph`` from job_steps rows unioned
    with the declared step template, falling back to [n/N] log parsing for
    jobs that predate the table. Only errors with 404 (this team's job, or
    not found — never 403).
    """
    job = _get(db, scope, job_id)
    return PipelineGraphOut(**build_job_graph(db, job))
