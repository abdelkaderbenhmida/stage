"""Warm cluster pool (docs/TODO.md Task 2.5).

Provisioning from cold takes minutes, which is longer than a developer waiting
on a test environment will tolerate. Keeping a few pre-provisioned clusters
idle lets a request be satisfied instantly, with a replacement warmed in the
background.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PooledCluster(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "pooled_clusters"
    __table_args__ = (
        # The claim query filters on exactly these two columns.
        Index("ix_pooled_clusters_spec_hash_status", "spec_hash", "status"),
    )

    # Hash of the normalised InfraSpec this cluster was built from: a pooled
    # cluster may only satisfy a request whose spec matches exactly, otherwise
    # the caller silently gets different infrastructure than they asked for.
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="warming", nullable=False)
    claimed_by_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    node_ips: Mapped[str | None] = mapped_column(Text)
