"""Repository layer enforcing tenant isolation (docs/PLATFORM_SPEC.md §7.3).

Every query that touches projects, nodes, deployments, jobs, scans, or
findings is scoped to the caller's **team memberships** (docs/TODO.md Task
3.1), with a fallback to direct ownership for projects created before teams
existed. Resources the caller cannot see surface as 404 (never 403), so a
cross-tenant probe cannot even confirm that a resource exists.

Every repository constructor requires an explicit ``Scope`` argument. A
forgotten filter must be a type error, not a silent leak — there is no default
scope anywhere in the API layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from controlplane.core.roles import ACTION_ROLES
from controlplane.models import TeamMember, role_at_least

if TYPE_CHECKING:
    from controlplane.models import Project


def paginate(session: Session, query: select, page: int = 1, page_size: int = 20):
    """Apply LIMIT/OFFSET to a select and return (items, total).

    Shared by the list endpoints (docs/TODO.md §8 item 6): the repositories
    keep returning rows and a total; the routers expose them as an
    ``X-Total-Count`` header plus ``Link`` rel=next/prev, so response bodies
    stay plain lists and existing clients keep working.
    """
    count_stmt = select(func.count()).select_from(query.order_by(None).subquery())
    total = session.scalar(count_stmt) or 0
    items = session.scalars(
        query.offset((page - 1) * page_size).limit(page_size)
    ).all()
    return list(items), total


class NotFoundError(Exception):
    """Resource not found or not accessible to the caller -> HTTP 404."""


class ForbiddenError(Exception):
    """Resource is visible to the caller but their team role cannot act on it.

    Raised only where the router already established that the resource exists,
    so a 403 here leaks nothing. Maps to HTTP 403.
    """

    def __init__(self, action: str, required: str, actual: str | None):
        self.action = action
        self.required = required
        self.actual = actual
        super().__init__(
            f"Action {action!r} requires role {required!r}; caller has {actual!r}."
        )


@dataclass(frozen=True)
class Scope:
    """Who a repository query may see, and what their team roles permit.

    ``team_ids`` is the isolation boundary: a bug here is a cross-tenant data
    leak. ``roles`` maps each team the caller belongs to to their role in it,
    so the repository layer can enforce write permissions without another
    round-trip. Both are snapshots taken at request start.
    """

    user_id: uuid.UUID | None
    team_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    roles: frozenset[tuple[uuid.UUID, str]] = field(default_factory=frozenset)

    @classmethod
    def from_session(cls, session: Session, user_id: uuid.UUID) -> Scope:
        """Build the scope for an authenticated user from the DB."""
        memberships = session.scalars(
            select(TeamMember).where(TeamMember.user_id == user_id)
        ).all()
        return cls(
            user_id=user_id,
            team_ids=frozenset(m.team_id for m in memberships),
            roles=frozenset((m.team_id, m.role) for m in memberships),
        )

    @classmethod
    def system(cls) -> Scope:
        """An unauthenticated, unrestricted scope.

        Used by Celery workers acting on behalf of a job that was already
        authorized at queue time (provision, reaper, scans). Never in routers —
        a router forgetting a scope is a leak, and this makes the forgetting
        explicit.
        """
        return cls(user_id=None)

    @property
    def is_system(self) -> bool:
        return self.user_id is None

    def role_in(self, team_id: uuid.UUID) -> str | None:
        for member_team_id, role in self.roles:
            if member_team_id == team_id:
                return role
        return None

    # ------------------------------------------------------------ visibility

    def can_access(self, project: Project) -> bool:
        """True when the caller may see this project.

        Team membership is the sole boundary — every project has a non-null
        team_id (models/project.py), so ownership is no longer a fallback
        path. A user who leaves a team loses access to its projects even if
        they created one, which is the point.
        """
        if self.is_system:
            return True
        return project.team_id in self.team_ids

    def project_filter(self):
        """SQLAlchemy condition selecting exactly the visible projects."""
        from controlplane.models import Project

        return Project.team_id.in_(self.team_ids)

    # ------------------------------------------------------------- write gating

    def require_role(self, project: Project, action: str) -> None:
        """Raise ForbiddenError unless the caller's role in the project's team
        satisfies ``action``. Defence in depth — the routers enforce the same
        rule, and this catches a router that forgets its dependency.

        Callers must have established visibility first (``can_access``): this
        check never runs alone, so an unknown team never 404s here.
        """
        if self.is_system:
            return
        required = ACTION_ROLES[action]
        actual = self.role_in(project.team_id)
        if actual is None or not role_at_least(actual, required):
            raise ForbiddenError(action, required, actual)

    def guard(self, project: Project, action: str) -> None:
        """Visibility (404) + role (403) in one call, for repository writes."""
        if not self.can_access(project):
            raise NotFoundError()
        self.require_role(project, action)
