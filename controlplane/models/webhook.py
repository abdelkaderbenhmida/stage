"""Git webhook subscriptions (docs/TODO.md Task 2.4)."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WebhookSubscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "webhook_subscriptions"
    __table_args__ = (
        Index("ix_webhook_subscriptions_deployment_id", "deployment_id"),
        Index("ix_webhook_subscriptions_repo_branch", "repo_url", "branch"),
    )

    deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(20), default="github", nullable=False)
    # Shared secret used to verify the HMAC signature on incoming deliveries.
    # The webhook endpoint is public and unauthenticated by design, so this is
    # the only thing standing between a stranger and triggering builds.
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # When the environment was created for a pull request, closing or merging
    # that pull request destroys it.
    pull_request_number: Mapped[int | None] = mapped_column()
