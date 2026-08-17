from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Statuses a job passes through before it stops moving. A job in one of these
# still owns the resources it was queued for.
ACTIVE_STATUSES = ("queued", "running")


class Job(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_project_id_status", "project_id", "status"),
        # History of one deployment, newest first — the per-app pipeline view.
        Index("ix_jobs_deployment_id_created_at", "deployment_id", "created_at"),
        # One live deploy per deployment. Two deploys of the same service race
        # over the same image tag and the same `kubectl apply`, so whichever
        # finishes last wins and the winner is decided by scheduling luck.
        # Checking in Python is not enough: two requests can both read "no
        # active job" before either inserts, so the rule is enforced here.
        Index(
            "uq_jobs_active_deploy_per_deployment",
            "deployment_id",
            unique=True,
            postgresql_where=text(
                "type = 'deploy' AND status IN ('queued', 'running')"
            ),
        ),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="SET NULL")
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(Text)
    log: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    # Celery may reject/ignore a cancel; status stays authoritative in the DB.
    cancel_requested: Mapped[bool] = mapped_column(default=False, nullable=False)
    # X-Request-Id of the API call that queued this job (§7 correlation).
    request_id: Mapped[str | None] = mapped_column(String(64))
