"""One recorded step of a job's pipeline.

Rows are written by workers.tasks._step() at the marker sites the log uses
("[n/N] name") and are authoritative for the pipeline graph: the log is
truncated head-first at 200 kB on long runs, but a row survives truncation
and carries timestamps the log text cannot.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class JobStep(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "job_steps"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "step_index", name="uq_job_steps_job_id_step_index"
        ),
        Index("ix_job_steps_job_id", "job_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_total: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="running", nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
