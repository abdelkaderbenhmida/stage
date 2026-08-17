"""Deployment pipeline endpoints (docs/PLATFORM_SPEC.md §8 Deployments)."""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane.api.deps import audit, get_current_user, get_db, get_scope, pagination_headers
from controlplane.api.rbac import require_deployment_action, require_project_action
from controlplane.api.schemas import (
    DeploymentCreate,
    DeploymentOut,
    JobSummaryOut,
    Message,
    WebhookSecretOut,
    WebhookSubscriptionCreate,
    WebhookSubscriptionOut,
)
from controlplane.core.repo_url import InvalidRepoUrl, validate_repo_url
from controlplane.models import Deployment, Project, User, WebhookSubscription
from controlplane.repositories.base import NotFoundError, Scope
from controlplane.repositories.deployments import DeploymentRepository
from controlplane.repositories.jobs import JobRepository
from controlplane.repositories.projects import ProjectRepository
from controlplane.workers import tasks

router = APIRouter(tags=["deployments"])


def _require_project(db: Session, scope: Scope, project_id: uuid.UUID):
    try:
        return ProjectRepository(db, scope).get_project(project_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.") from None


@router.get("/projects/{project_id}/deployments", response_model=list[DeploymentOut])
def list_deployments(
    project_id: uuid.UUID,
    request: Request,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    _require_project(db, scope, project_id)
    items, total = DeploymentRepository(db, scope).list(project_id, page, page_size)
    response.headers.update(pagination_headers(request, total, page, page_size))
    return items


@router.post("/projects/{project_id}/deployments", response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
def create_deployment(
    body: DeploymentCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    project: Project = Depends(require_project_action("deployment.create")),
):
    """Create a deployment. Developers and above may deploy (docs/TODO.md §3.2)."""
    if project.status not in ("ready",):
        raise HTTPException(status_code=409, detail="Project must be provisioned (status 'ready') before deploying.") from None

    try:
        validate_repo_url(body.repo_url)
    except InvalidRepoUrl as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo = DeploymentRepository(db, scope)
    deployment = repo.create(
        project.id, body.service_name, body.repo_url, body.branch, body.port, body.replicas, body.strategy
    )
    db.commit()
    tasks.queue_deploy(deployment, project, user.id)
    db.commit()
    audit(db, user.id, "deployment.create", request, resource_type="deployment", resource_id=str(deployment.id), team_id=project.team_id)
    return deployment


@router.get("/deployments/{deployment_id}", response_model=DeploymentOut)
def get_deployment(
    deployment_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    try:
        return DeploymentRepository(db, scope).get(deployment_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Deployment not found.") from None


@router.delete("/deployments/{deployment_id}", response_model=Message)
def delete_deployment(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    deployment: Deployment = Depends(require_deployment_action("deployment.delete")),
):
    project = ProjectRepository(db, scope).get_project(deployment.project_id)
    tasks.queue_undeploy(deployment, project, user.id)
    DeploymentRepository(db, scope).delete(deployment.id)
    db.commit()
    audit(db, user.id, "deployment.delete", request, resource_type="deployment", resource_id=str(deployment.id), team_id=project.team_id)
    return Message(message="Removed from cluster.")


@router.post(
    "/deployments/{deployment_id}/webhook",
    response_model=WebhookSecretOut,
    status_code=status.HTTP_201_CREATED,
)
def create_webhook(
    body: WebhookSubscriptionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    deployment: Deployment = Depends(require_deployment_action("deployment.create")),
):
    """Subscribe this deployment to pushes on its repository.

    The generated secret is returned exactly once — it is what the provider
    signs deliveries with, and a listing endpoint that kept handing it back
    would let anyone who can read the API forge them.
    """
    secret = secrets.token_urlsafe(32)
    subscription = WebhookSubscription(
        deployment_id=deployment.id,
        provider=body.provider,
        secret=secret,
        repo_url=deployment.repo_url,
        branch=body.branch or deployment.branch,
        pull_request_number=body.pull_request_number,
    )
    db.add(subscription)
    db.commit()
    audit(
        db, user.id, "webhook.create", request,
        resource_type="deployment", resource_id=str(deployment.id),
    )
    db.commit()

    return WebhookSecretOut(
        id=subscription.id,
        deployment_id=subscription.deployment_id,
        provider=subscription.provider,
        repo_url=subscription.repo_url,
        branch=subscription.branch,
        active=subscription.active,
        created_at=subscription.created_at,
        secret=secret,
    )


@router.get("/deployments/{deployment_id}/webhook", response_model=list[WebhookSubscriptionOut])
def list_webhooks(
    deployment_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    repo = DeploymentRepository(db, scope)
    try:
        repo.get(deployment_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Deployment not found.") from None
    return list(
        db.scalars(
            select(WebhookSubscription).where(WebhookSubscription.deployment_id == deployment_id)
        )
    )


@router.get("/deployments/{deployment_id}/jobs", response_model=list[JobSummaryOut])
def list_deployment_jobs(
    deployment_id: uuid.UUID,
    request: Request,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    """This deployment's pipeline history, newest first.

    Every run was already recorded against the deployment; without this
    endpoint nothing could read them back, so a tenant could only ever see the
    run they happened to be watching.
    """
    try:
        items, total = JobRepository(db, scope).list_for_deployment(
            deployment_id, page, page_size
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Deployment not found.") from None
    response.headers.update(pagination_headers(request, total, page, page_size))
    return items


@router.post("/deployments/{deployment_id}/redeploy", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
def redeploy(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    deployment: Deployment = Depends(require_deployment_action("deployment.create")),
):
    project = ProjectRepository(db, scope).get_project(deployment.project_id)

    repo = DeploymentRepository(db, scope)
    previous_status = deployment.status
    repo.set_status(deployment, "queued")
    db.commit()
    try:
        tasks.queue_deploy(deployment, project, user.id)
    except tasks.DeployAlreadyRunning as exc:
        # The status was moved to "queued" for a run that will not exist.
        repo.set_status(deployment, previous_status)
        db.commit()
        raise HTTPException(
            status_code=409,
            detail=(
                f"A deploy of this service is already {exc.job.status} "
                f"(job {exc.job.id}). Wait for it to finish, or cancel it first."
            ),
        ) from None
    db.commit()
    audit(db, user.id, "deployment.redeploy", request, resource_type="deployment", resource_id=str(deployment.id), team_id=project.team_id)
    return Message(message="Redeploy queued.")