"""Role-based access control (docs/TODO.md Task 3.2).

Permissions are checked against the caller's role in the team that owns the
resource. Two rules matter:

1. **404, never 403, for a resource the caller cannot see at all.** A 403
   confirms the resource exists.
2. **403 for a resource the caller *can* see but lacks the role to change.**
   Here the existence is already known, so hiding it gains nothing and an
   accurate error is far more useful.

The UI hides actions a user cannot take, but that is cosmetic — every check
below runs server-side regardless of what the client sent. The repository
layer repeats these checks (defence in depth), sharing the same action->role
mapping from ``controlplane.core.roles``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from controlplane.api.deps import get_current_user, get_current_user_sse

# Re-exported so callers keep a single import point. The mapping itself lives
# in core/ so the repository layer can enforce the same table.
from controlplane.core.roles import ACTION_ROLES, PLATFORM_ADMIN_ROLE  # noqa: F401
from controlplane.db import get_db
from controlplane.models import Deployment, Project, User
from controlplane.repositories.base import ForbiddenError, NotFoundError, Scope


def _require_role_or_404(scope: Scope, project: Project, action: str) -> None:
    """Map the repository layer's errors onto HTTP for the dependency layer."""
    try:
        scope.guard(project, action)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.") from None
    except ForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This action requires the '{exc.required}' role.",
        ) from None


def require_team_role(team_id: uuid.UUID, user: User, db: Session, action: str) -> None:
    """Raise unless the user's role in ``team_id`` satisfies ``action``."""
    required = ACTION_ROLES.get(action)
    if required is None:
        raise HTTPException(status_code=500, detail=f"Unknown action {action!r}.")

    scope = Scope.from_session(db, user.id)
    role = scope.role_in(team_id)
    if role is None:
        # Not a member: behave as though the team does not exist.
        raise HTTPException(status_code=404, detail="Not found.")
    from controlplane.models import role_at_least

    if not role_at_least(role, required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This action requires the '{required}' role; you have '{role}'.",
        )


def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    """Gate the entire ``/platform`` operator console.

    Unlike every other check in this module, this is not a per-team role —
    it's ``User.role``, a global column OIDC already populates. A caller who
    fails this is not a member of anything special; they're just not an
    operator, so 404 (not 403) to avoid confirming the console exists.
    """
    if user.role != PLATFORM_ADMIN_ROLE:
        raise HTTPException(status_code=404, detail="Not found.")
    return user


def require_platform_admin_sse(user: User = Depends(get_current_user_sse)) -> User:
    """``require_platform_admin`` for an EventSource stream.

    Identical rule (global ``User.role``, 404 rather than 403), but resolves
    the caller through ``get_current_user_sse`` so the token may arrive as a
    query parameter — the browser's EventSource API cannot set an
    Authorization header, which is the same constraint the job log stream
    already works around.
    """
    if user.role != PLATFORM_ADMIN_ROLE:
        raise HTTPException(status_code=404, detail="Not found.")
    return user


def require_project_action(action: str) -> Callable:
    """Build a dependency enforcing ``action`` on the path's project.

    Usage:

        @router.post("/projects/{project_id}/provision")
        def provision(project=Depends(require_project_action("project.provision"))):
            ...
    """

    def dependency(
        project_id: uuid.UUID,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> Project:
        scope = Scope.from_session(db, user.id)
        project = db.get(Project, project_id)
        if project is None or not scope.can_access(project):
            raise HTTPException(status_code=404, detail="Project not found.")
        _require_role_or_404(scope, project, action)
        return project

    return dependency


def require_deployment_action(action: str) -> Callable:
    """Build a dependency enforcing ``action`` on the path's deployment.

    The deployment's project is resolved first and the role check runs against
    that project's team — a deployment is never more permissive than its
    project.
    """

    def dependency(
        deployment_id: uuid.UUID,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> Deployment:
        scope = Scope.from_session(db, user.id)
        deployment = db.get(Deployment, deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment not found.")
        project = db.get(Project, deployment.project_id)
        if project is None or not scope.can_access(project):
            raise HTTPException(status_code=404, detail="Deployment not found.")
        _require_role_or_404(scope, project, action)
        return deployment

    return dependency
