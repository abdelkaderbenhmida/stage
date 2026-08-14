from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Deployment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "deployments"
    __table_args__ = (Index("ix_deployments_project_id", "project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    service_name: Mapped[str] = mapped_column(String(120), nullable=False)
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str] = mapped_column(String(120), default="main", nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    image_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", nullable=False
    )
    live_url: Mapped[str | None] = mapped_column(Text)
    replicas: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    # Progressive delivery (docs/TODO.md Task 5.2): "deployment" is the plain
    # Deployment rollout; "canary" and "bluegreen" render an Argo Rollout with
    # an analysis step against the platform's Prometheus SLOs.
    strategy: Mapped[str] = mapped_column(String(20), default="deployment", nullable=False)
