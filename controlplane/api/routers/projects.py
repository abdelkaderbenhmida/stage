"""Project CRUD (docs/PLATFORM_SPEC.md §8 Projects)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from controlplane.api.deps import audit, get_current_user, get_db, get_scope, pagination_headers
from controlplane.api.rbac import require_project_action, require_team_role
from controlplane.api.schemas import (
    DestroyRequest,
    ExtendRequest,
    ProjectCreate,
    ProjectListOut,
    ProjectOut,
    ProjectPatch,
)
from controlplane.core.config import settings
from controlplane.core.presets import expand_preset
from controlplane.models import Project, User
from controlplane.repositories.base import NotFoundError, Scope
from controlplane.repositories.projects import ProjectRepository
from controlplane.repositories.teams import ensure_personal_team
from controlplane.schemas.spec import InfraSpec

router = APIRouter(tags=["projects"])


def _to_out(db: Session, project: Project, scope: Scope) -> ProjectOut:
    nodes = ProjectRepository(db, scope).nodes(project.id)
    return ProjectOut(
        id=project.id,
        owner_id=project.owner_id,
        team_id=project.team_id,
        name=project.name,
        description=project.description,
        status=project.status,
        infra_spec=project.infra_spec,
        workspace_path=project.workspace_path,
        created_at=project.created_at,
        updated_at=project.updated_at,
        nodes=list(nodes),
        ttl_hours=project.ttl_hours,
        expires_at=project.expires_at,
        auto_destroy=project.auto_destroy,
        expiry_warned=project.expiry_warned,
    )


def _touch(db: Session, project: Project) -> None:
    """Record activity so the reaper does not delete an environment in use."""
    from datetime import UTC, datetime

    project.last_accessed_at = datetime.now(UTC)
    db.commit()


@router.get("/projects", response_model=list[ProjectListOut])
def list_projects(
    request: Request,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    items, total = ProjectRepository(db, scope).list_projects(page, page_size)
    response.headers.update(pagination_headers(request, total, page, page_size))
    return items


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
):
    repo = ProjectRepository(db, scope)
    if repo.count() >= settings.max_projects_per_user:
        raise HTTPException(
            status_code=409,
            detail=f"Project cap of {settings.max_projects_per_user} reached.",
        )
    if repo.get_by_name(body.name):
        raise HTTPException(status_code=409, detail="A project with this name already exists.")

    # A preset is expanded here and then validated by the same InfraSpec model
    # as hand-written input — presets are a convenience, not a trust boundary.
    if body.preset is not None:
        try:
            spec = InfraSpec.model_validate(expand_preset(body.preset, body.name))
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        spec = body.infra_spec

    if body.mode is not None:
        spec = spec.model_copy(update={"mode": body.mode})

    team_id = body.team_id
    if team_id is not None:
        require_team_role(team_id, user, db, "project.create")
    else:
        # Default to the caller's personal team so a solo developer needs no
        # setup before their first project.
        team_id = ensure_personal_team(db, user).id

    project = repo.create(body.name, spec.model_dump(mode="json"), team_id, body.description)
    project.auto_destroy = body.auto_destroy
    if body.ttl_hours is not None:
        project.ttl_hours = body.ttl_hours
    elif spec.mode == "namespace":
        project.ttl_hours = settings.default_ttl_hours
    else:
        project.ttl_hours = settings.default_vm_ttl_hours
    repo.create_nodes(project.id, [node.model_dump() for node in spec.nodes])
    db.commit()
    audit(db, user.id, "project.create", request, resource_type="project", resource_id=str(project.id), team_id=project.team_id)
    return _to_out(db, project, scope)


@router.post("/projects/{project_id}/extend", response_model=ProjectOut)
def extend_project(
    request: Request,
    body: ExtendRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    project: Project = Depends(require_project_action("project.extend")),
):
    """Push back an environment's expiry (docs/TODO.md Task 2.2).

    Capped at `max_ttl_hours` from creation so repeated extensions cannot turn
    an ephemeral environment into a permanent one by accident.
    """
    from datetime import UTC, datetime, timedelta

    created = project.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    ceiling = created + timedelta(hours=settings.max_ttl_hours)

    # Measured from now once the expiry is already behind us. Extending from a
    # stale expires_at meant "extend by 24 hours" on an environment that
    # lapsed 14 hours ago bought 10 hours, which is not what the prompt says
    # and not what the caller asked for. The max_ttl ceiling below is what
    # stops repeated extensions, so nothing is lost by starting from now.
    now = datetime.now(UTC)
    base = project.expires_at or now
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    base = max(base, now)

    proposed = base + timedelta(hours=body.hours)
    if proposed > ceiling:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Total lifetime is capped at {settings.max_ttl_hours}h; "
                f"this project cannot be extended beyond {ceiling.isoformat()}."
            ),
        )

    project.expires_at = proposed
    project.expiry_warned = False
    project.ttl_hours = project.ttl_hours + body.hours
    db.commit()
    audit(
        db, user.id, "project.extend", request,
        resource_type="project", resource_id=str(project.id), detail={"hours": body.hours},
        team_id=project.team_id,
    )
    db.commit()
    return _to_out(db, project, scope)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    try:
        project = ProjectRepository(db, scope).get_project(project_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.") from None
    # Reading a project counts as using it — an environment someone is
    # actively working in must not be reaped out from under them.
    _touch(db, project)
    return _to_out(db, project, scope)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(
    body: ProjectPatch,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    project: Project = Depends(require_project_action("project.update")),
):
    if project.status not in ("draft", "ready"):
        raise HTTPException(status_code=409, detail=f"Project cannot be edited while status is {project.status}.")

    repo = ProjectRepository(db, scope)
    if body.infra_spec is not None:
        project = repo.update_spec(project, body.infra_spec.model_dump(mode="json"), body.description)
        repo.replace_nodes(project.id, [node.model_dump() for node in body.infra_spec.nodes])
    elif body.description is not None:
        project = repo.update_spec(project, project.infra_spec, body.description)
    db.commit()
    audit(db, user.id, "project.update", request, resource_type="project", resource_id=str(project.id), team_id=project.team_id)
    return _to_out(db, project, scope)


@router.delete("/projects/{project_id}", response_model=dict)
def delete_project(
    body: DestroyRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    project: Project = Depends(require_project_action("project.destroy")),
):
    """Queue a destroy job then delete the record (§8). Requires the project
    name to be typed in the body as confirmation (Phase 7)."""
    if body.confirm_name != project.name:
        raise HTTPException(status_code=422, detail="confirmation name does not match the project.") from None

    # Captured before delete+commit: the ORM expires every attribute on a
    # deleted, committed instance, so reading project.id/team_id afterward
    # for the audit call below would re-query a row that no longer exists.
    project_id, team_id, workspace = project.id, project.team_id, project.workspace_path
    from controlplane.workers.tasks import queue_destroy

    queue_destroy(project_id, workspace, project.name, user.id)
    db.delete(project)
    db.commit()
    audit(db, user.id, "project.delete", request, resource_type="project", resource_id=str(project_id), team_id=team_id)
    return {"message": "Destroy queued."}