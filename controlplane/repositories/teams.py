"""Team membership queries (docs/TODO.md Task 3.1, 3.2)."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane.models import Team, TeamMember, User, role_at_least
from controlplane.repositories.base import NotFoundError

_SLUG_STRIP = re.compile(r"[^a-z0-9-]+")


def slugify(value: str) -> str:
    slug = _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")
    return slug[:60] or "team"


class TeamRepository:
    def __init__(self, session: Session, user_id: uuid.UUID):
        self.session = session
        self.user_id = user_id

    # ------------------------------------------------------------ read side

    def memberships(self) -> list[TeamMember]:
        return list(
            self.session.scalars(select(TeamMember).where(TeamMember.user_id == self.user_id))
        )

    def team_ids(self) -> list[uuid.UUID]:
        """Every team the caller belongs to.

        This is the isolation boundary: repositories scope their queries to
        this list, so a bug here is a cross-tenant data leak.
        """
        return [membership.team_id for membership in self.memberships()]

    def role_in(self, team_id: uuid.UUID) -> str | None:
        membership = self.session.scalar(
            select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id == self.user_id
            )
        )
        return membership.role if membership else None

    def has_role(self, team_id: uuid.UUID, required: str) -> bool:
        actual = self.role_in(team_id)
        return actual is not None and role_at_least(actual, required)

    def get_team(self, team_id: uuid.UUID) -> Team:
        team = self.session.get(Team, team_id)
        # Not a member => 404, never 403: a 403 would confirm the team exists.
        if team is None or self.role_in(team_id) is None:
            raise NotFoundError()
        return team

    def list_teams(self) -> list[Team]:
        ids = self.team_ids()
        if not ids:
            return []
        return list(self.session.scalars(select(Team).where(Team.id.in_(ids))))

    def members(self, team_id: uuid.UUID) -> list[tuple[TeamMember, User]]:
        self.get_team(team_id)
        rows = self.session.execute(
            select(TeamMember, User)
            .join(User, User.id == TeamMember.user_id)
            .where(TeamMember.team_id == team_id)
        ).all()
        return [(membership, user) for membership, user in rows]

    # ----------------------------------------------------------- write side

    def create_team(self, name: str, description: str | None = None) -> Team:
        team = Team(name=name, slug=slugify(name), description=description)
        self.session.add(team)
        self.session.flush()
        # The creator is the first admin, otherwise nobody could administer it.
        self.session.add(TeamMember(team_id=team.id, user_id=self.user_id, role="admin"))
        self.session.flush()
        return team

    def add_member(self, team_id: uuid.UUID, user_id: uuid.UUID, role: str) -> TeamMember:
        self.get_team(team_id)
        existing = self.session.scalar(
            select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        if existing:
            existing.role = role
            self.session.flush()
            return existing
        membership = TeamMember(team_id=team_id, user_id=user_id, role=role)
        self.session.add(membership)
        self.session.flush()
        return membership

    def remove_member(self, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
        membership = self.session.scalar(
            select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        if membership is None:
            raise NotFoundError()
        # Removing the last admin would leave the team unadministrable.
        if membership.role == "admin":
            admins = self.session.scalars(
                select(TeamMember).where(
                    TeamMember.team_id == team_id, TeamMember.role == "admin"
                )
            ).all()
            if len(admins) <= 1:
                raise ValueError("Cannot remove the last admin of a team.")
        self.session.delete(membership)
        self.session.flush()


def ensure_personal_team(session: Session, user: User) -> Team:
    """Give a newly registered user a team so they can create a project at once.

    Without this, registration would land the user in a state where every
    project endpoint fails because they belong to no team.
    """
    slug = f"personal-{str(user.id).replace('-', '')}"
    existing = session.scalar(select(Team).where(Team.slug == slug))
    if existing:
        return existing
    team = Team(
        name=user.email.split("@")[0],
        slug=slug,
        description="Personal team, created automatically at registration.",
        is_personal=True,
    )
    session.add(team)
    session.flush()
    session.add(TeamMember(team_id=team.id, user_id=user.id, role="admin"))
    session.flush()
    return team
