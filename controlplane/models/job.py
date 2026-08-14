from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Job(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_project_id_status", "project_id", "status"),)

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
