from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),
        Index("ix_projects_owner_id", "owner_id"),
    )

    # Retained for audit ("who created this"), no longer the isolation
    # boundary — team_id is (docs/TODO.md Task 3.1).
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="draft", nullable=False
    )
    infra_spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    workspace_path: Mapped[str | None] = mapped_column(Text)

    # Ephemeral-environment lifecycle (docs/TODO.md Task 2.2). An environment
    # that is never automatically destroyed is just an expensive permanent one,
    # so every project carries a TTL clock from the moment it becomes ready.
    ttl_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_destroy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set by the reaper when the environment is close to expiry, so the UI can
    # warn rather than deleting someone's work without notice.
    expiry_warned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Node(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "nodes"
    __table_args__ = (Index("ix_nodes_project_id", "project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(20), nullable=False)
    vcpu: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Alias matching Postgres INET semantics without forcing the type onto tests
    @property
    def ip_display(self) -> str | None:
        return self.ip_address
