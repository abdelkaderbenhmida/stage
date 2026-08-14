"""Celery application for the control plane (docs/PLATFORM_SPEC.md §4).

Graceful shutdown (§7): when the worker process receives SIGTERM, any job
currently ``running`` in *this* worker is marked ``interrupted`` instead of
being left ``running`` forever — a stale ``running`` row blocks the reaper
and every cancel path.
"""

import logging
import uuid
from datetime import UTC, datetime

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_postrun, task_prerun, worker_shutting_down

from controlplane.core.config import settings
from controlplane.core.logging import log_extra, setup_logging

setup_logging(settings.log_format)

# Fail closed before the worker accepts anything (§7 item 2): a worker that
# starts without the signing key would mint nothing but garbled jobs.
settings.require_secrets()

logger = logging.getLogger("controlplane.worker")

celery_app = Celery(
    "controlplane",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["controlplane.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    worker_hijack_root_logger=False,
    task_time_limit=settings.provision_timeout_seconds + 120,
    task_soft_time_limit=settings.provision_timeout_seconds + 60,
    beat_schedule={
        "poll-node-health": {
            "task": "controlplane.workers.tasks.poll_nodes",
            "schedule": crontab(minute="*/1"),
        },
        "beat-heartbeat": {
            "task": "controlplane.workers.tasks.beat_pulse",
            "schedule": 15.0,
        },
        # Without this, ephemeral environments live forever and cost grows
        # without bound (docs/TODO.md Task 2.2).
        "reap-expired-projects": {
            "task": "controlplane.workers.tasks.reap_expired_projects",
            "schedule": crontab(minute="*/10"),
        },
        "replenish-warm-pool": {
            "task": "controlplane.workers.tasks.replenish_pool",
            "schedule": crontab(minute="*/15"),
        },
    },
)

# ---------------------------------------------------------------------------
# Graceful shutdown: mark in-process running jobs interrupted on SIGTERM.
# ---------------------------------------------------------------------------

_JOB_TASKS = {
    "controlplane.workers.tasks.provision_task",
    "controlplane.workers.tasks.destroy_task",
    "controlplane.workers.tasks.scan_task",
    "controlplane.workers.tasks.deploy_task",
    "controlplane.workers.tasks.undeploy_task",
}

# Job ids of tasks executing in THIS worker process. Only these are safe to
# mark interrupted on shutdown: a different worker still has its own SIGTERM
# to handle, and touching its running jobs would be a lie.
_running_jobs: set[str] = set()


def _job_id_of(task_name: str, args) -> str | None:
    if task_name in _JOB_TASKS and args:
        return str(args[0])
    return None


@task_prerun.connect
def _track_started(task_id: str, task, args, **kwargs) -> None:
    job_id = _job_id_of(task.name, args)
    if job_id:
        _running_jobs.add(job_id)


@task_postrun.connect
def _track_finished(task_id: str, task, args, **kwargs) -> None:
    job_id = _job_id_of(task.name, args)
    if job_id:
        _running_jobs.discard(job_id)


@worker_shutting_down.connect
def _mark_interrupted(**kwargs) -> None:
    if not _running_jobs:
        return
    try:
        from controlplane.db import SessionLocal
        from controlplane.models import Job

        with SessionLocal() as db:
            now = datetime.now(UTC)
            for job_id in _running_jobs:
                job = db.get(Job, uuid.UUID(job_id))
                if job is not None and job.status == "running":
                    job.status = "interrupted"
                    job.finished_at = now
                    job.error_message = "Worker interrupted by shutdown"
            db.commit()
    except Exception:
        logger.exception("failed to mark %d running job(s) interrupted on shutdown", len(_running_jobs))
    logger.info(
        "marked %d running job(s) interrupted on shutdown",
        len(_running_jobs),
        extra=log_extra(jobs=sorted(_running_jobs)),
    )