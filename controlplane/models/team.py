"""Teams and membership (docs/TODO.md Task 3.1).

An internal developer platform is used by teams, not by isolated individuals.
Projects belong to a team; ``Project.owner_id`` is retained as the creating
user purely for audit purposes and is no longer the isolation boundary.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Ordered least- to most-privileged. Used by require_role() to decide whether a
# member's role satisfies a required one.
ROLE_ORDER = ("viewer", "developer", "owner", "admin")


def role_at_least(actual: str, required: str) -> bool:
    """True when ``actual`` is at or above ``required`` in the role hierarchy.

    An unknown role grants nothing — fail closed rather than treating an
    unrecognised value as permissive.
    """
    if actual not in ROLE_ORDER or required not in ROLE_ORDER:
        return False
    return ROLE_ORDER.index(actual) >= ROLE_ORDER.index(required)


class Team(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("slug", name="uq_teams_slug"),)

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Personal teams are created automatically for each user so that a single
    # developer needs no setup before creating their first project.
    is_personal: Mapped[bool] = mapped_column(default=False, nullable=False)


class TeamMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
        Index("ix_team_members_user_id", "user_id"),
        Index("ix_team_members_team_id", "team_id"),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), default="developer", nullable=False)
