from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


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