from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Deployment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "deployments"
    __table_args__ = (
        Index("ix_deployments_project_id", "project_id"),
        # A service name identifies one service inside a project. Enforced in
        # the database as well as in the repository, because a duplicate here
        # means two rows describing one Kubernetes Deployment.
        UniqueConstraint("project_id", "service_name", name="uq_deployments_project_service"),
    )

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

    # Non-secret configuration handed to the container as environment
    # variables. Without this a tenant cannot configure their app at all —
    # no database URL, no feature flag, no log level — which made the platform
    # only able to run applications that need no configuration.
    env_vars: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")

    # Names only. The values live in the secret store (Vault), never here:
    # a database row is the wrong place for a tenant's credentials, and this
    # column is read by any code that can see the deployment.
    secret_keys: Mapped[list] = mapped_column(JSONB, default=list, nullable=False, server_default="[]")

    # Probe path. This was hardcoded to /livez, so every tenant application
    # was required to implement that exact endpoint or fail to become ready
    # with nothing explaining why.
    health_path: Mapped[str] = mapped_column(String(200), default="/livez", nullable=False, server_default="/livez")
