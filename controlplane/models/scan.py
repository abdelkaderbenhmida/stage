from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Scan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scans"
    __table_args__ = (
        Index("ix_scans_project_id_created_at", "project_id", "created_at"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="SET NULL")
    )
    tool: Mapped[str] = mapped_column(String(20), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    raw_output: Mapped[dict | None] = mapped_column(JSONB)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    # The security summary endpoint aggregates across scan.findings; without
    # this relationship that endpoint raises AttributeError for any project
    # that has ever been scanned.
    findings: Mapped[list[Finding]] = relationship(
        back_populates="scan",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Finding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_scan_id_severity", "scan_id", "severity"),)

    scan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    scan: Mapped[Scan] = relationship(back_populates="findings")
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    identifier: Mapped[str | None] = mapped_column(Text)
    package_name: Mapped[str | None] = mapped_column(Text)
    installed_version: Mapped[str | None] = mapped_column(Text)
    fixed_version: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    line_number: Mapped[int | None] = mapped_column(Integer)
