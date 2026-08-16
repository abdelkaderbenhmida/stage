"""Infrastructure write path: provision / destroy / plan / nodes.

All provisioning work runs in Celery; the HTTP request only queues a job.
Provisioning and destroy require the owner role (docs/TODO.md §3.2 table);
the read endpoints are open to any team member.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from controlplane.api.deps import audit, get_current_user, get_db, get_scope
from controlplane.api.rate_limit import check_rate_limit
from controlplane.api.rbac import require_project_action
from controlplane.api.schemas import DestroyRequest, NodeOut
from controlplane.core.config import settings
from controlplane.models import Project, User
from controlplane.repositories.base import NotFoundError, Scope
from controlplane.repositories.projects import ProjectRepository
from controlplane.workers import tasks

router = APIRouter(tags=["infrastructure"])


def _require_project(repo: ProjectRepository, project_id: uuid.UUID):
    try:
        return repo.get_project(project_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.") from None


@router.post("/projects/{project_id}/provision", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
def provision(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    project: Project = Depends(require_project_action("project.provision")),
):
    repo = ProjectRepository(db, scope)

    if not check_rate_limit(f"provision:{user.id}", settings.provision_per_hour, 3600):
        raise HTTPException(status_code=429, detail="Provisioning limit reached for this hour.") from None
    if repo.get_active_provision_job(project.id):
        raise HTTPException(status_code=409, detail="A provisioning/destroy job is already running.") from None

    # Host capacity, not a per-tenant limit: a dedicated cluster costs real
    # RAM/CPU regardless of which team owns it, so this counts across every
    # tenant, not just the caller's scope.
    mode = (project.infra_spec or {}).get("mode", "vm")
    if mode != "namespace":
        active = (
            db.query(Project.infra_spec)
            .filter(Project.status.in_(("ready", "provisioning")))
            .all()
        )
        active_vm_clusters = sum(
            1 for (spec,) in active if (spec or {}).get("mode", "vm") != "namespace"
        )
        if active_vm_clusters >= settings.max_concurrent_vm_clusters:
            raise HTTPException(
                status_code=429,
                detail="Dedicated-cluster capacity reached; try again once another environment is destroyed.",
            ) from None

    job = tasks.queue_provision(project, user.id)
    repo.set_status(project, "provisioning")
    db.commit()
    audit(db, user.id, "project.provision", request, resource_type="project", resource_id=str(project.id), team_id=project.team_id)
    return {"job_id": str(job.id)}


@router.post("/projects/{project_id}/destroy", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
def destroy(
    body: DestroyRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    project: Project = Depends(require_project_action("project.destroy")),
):
    repo = ProjectRepository(db, scope)
    if body.confirm_name != project.name:
        raise HTTPException(status_code=422, detail="confirmation name does not match the project.") from None
    if repo.get_active_provision_job(project.id):
        raise HTTPException(status_code=409, detail="A provisioning/destroy job is already running.") from None

    job = tasks.queue_destroy(project.id, project.workspace_path, project.name, user.id)
    repo.set_status(project, "destroying")
    db.commit()
    audit(db, user.id, "project.destroy", request, resource_type="project", resource_id=str(project.id), team_id=project.team_id)
    return {"job_id": str(job.id)}


@router.get("/projects/{project_id}/nodes", response_model=list[NodeOut])
def list_nodes(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    repo = ProjectRepository(db, scope)
    _require_project(repo, project_id)
    return repo.nodes(project_id)


@router.get("/projects/{project_id}/plan", response_model=dict)
def plan(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
):
    """Dry run: render templates and run ``terraform plan``, return the diff."""
    repo = ProjectRepository(db, scope)
    project = _require_project(repo, project_id)

    from controlplane.core.runtime import project_workspace, terraform_runtime
    from controlplane.renderers import render_terraform
    from controlplane.runners.terraform_runner import terraform_init, terraform_plan
    from controlplane.schemas.spec import InfraSpec

    parsed = InfraSpec.model_validate(project.infra_spec)
    ws = project_workspace(project.id)
    render_terraform(parsed, terraform_runtime(user.id), ws)

    init = terraform_init(ws)
    if init.exit_code != 0:
        return {"exit_code": init.exit_code, "output": init.output, "applied": False}
    plan_result = terraform_plan(ws)
    return {"exit_code": plan_result.exit_code, "output": plan_result.output, "applied": False}