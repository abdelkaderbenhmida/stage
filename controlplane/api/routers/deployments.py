"""Deployment pipeline endpoints (docs/PLATFORM_SPEC.md §8 Deployments)."""

import asyncio
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane import platform_ops
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
from controlplane.core.app_config import (
    ConfigError,
    assert_disjoint,
    delete_secrets,
    store_secrets,
    validate_env,
)
from controlplane.core.repo_url import InvalidRepoUrl, validate_repo_url
from controlplane.core.validation import k8s_namespace
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


@router.get("/projects/{project_id}/ci")
async def project_ci(
    project_id: uuid.UUID,
    limit: int = Query(default=10, ge=1, le=30),
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    """GitHub Actions runs for the repositories *this project* deploys.

    Tenant-facing counterpart to the admin console's CI tab. That one reads
    this control plane's own git remote and shows the platform's pipeline —
    correct for an operator, useless (and cross-tenant) for a user asking
    "what did CI do with *my* app". Here the repositories come from the
    caller's own deployments, and `_require_project` 404s a project the
    caller cannot see, so one user can never read another's CI.

    Fails soft per repository: a repo whose runs cannot be read (no gh, no
    workflows, private without a credential) reports its own error instead
    of failing the whole response.
    """
    _require_project(db, scope, project_id)
    deployments = db.scalars(
        select(Deployment).where(Deployment.project_id == project_id)
    ).all()

    seen: dict[str, list[str]] = {}
    for deployment in deployments:
        try:
            slug = platform_ops.repo_slug_from_url(deployment.repo_url)
        except platform_ops.ServiceError:
            continue
        seen.setdefault(slug, []).append(deployment.service_name)

    # One `gh` subprocess per repository, fanned out rather than run in
    # sequence: each call has its own 25s timeout, so a project with a
    # handful of repositories would otherwise add those timeouts together
    # and hold a worker for minutes when GitHub is slow or unreachable.
    # Concurrently the whole endpoint costs about as much as its slowest
    # repository instead of the sum of all of them.
    ordered = sorted(seen.items())
    results = await asyncio.gather(
        *(asyncio.to_thread(platform_ops.ci_runs_for_repo, slug, limit) for slug, _ in ordered)
    )
    repos = [
        {**result, "services": sorted(services)}
        for (_, services), result in zip(ordered, results, strict=True)
    ]
    return {"repos": repos}


@router.get("/projects/{project_id}/workloads")
def project_workloads(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    """What is actually running in *this project's* namespace, plus its pods.

    The tenant-facing equivalent of the admin console's ArgoCD tab: ArgoCD
    only manages the platform's own services, while a tenant's app is applied
    with plain kubectl into its own namespace, so "my app's rollout status"
    has to come from the namespace itself. The namespace is derived from the
    project id server-side — never from the client — so this cannot be
    pointed at another tenant's namespace, and `_require_project` 404s a
    project the caller cannot see.
    """
    project = _require_project(db, scope, project_id)
    namespace = k8s_namespace(project.id)
    workloads = platform_ops.namespace_workloads(namespace)
    pods = platform_ops.pods_status(namespace)
    return {
        **workloads,
        "pods": pods.get("pods", []),
        "pods_reachable": pods.get("reachable", False),
    }


@router.get("/projects/{project_id}/secrets")
def project_secrets(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    """Secret NAMES held for each of this project's deployments.

    Names only, never values: the console needs to show which secrets a
    deployment carries without ever putting them on the wire (the same rule
    the seeding path follows). Values live in the secret store keyed by team,
    so a caller who cannot see the project cannot reach them either.
    """
    project = _require_project(db, scope, project_id)
    deployments = db.scalars(
        select(Deployment).where(Deployment.project_id == project_id)
    ).all()
    return {
        "deployments": [
            {
                "id": str(d.id),
                "service_name": d.service_name,
                "secret_keys": sorted(d.secret_keys or []),
                "env_keys": sorted((d.env_vars or {}).keys()),
            }
            for d in sorted(deployments, key=lambda d: d.service_name)
        ]
    }


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

    # Configuration is validated before anything is written, so a bad name
    # cannot leave a half-configured deployment behind.
    try:
        env_vars = validate_env(body.env)
        secrets = validate_env(body.secrets, kind="secret")
        assert_disjoint(env_vars, secrets)
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    repo = DeploymentRepository(db, scope)
    deployment = repo.create(
        project.id, body.service_name, body.repo_url, body.branch, body.port, body.replicas, body.strategy
    )
    deployment.env_vars = env_vars
    deployment.health_path = body.health_path
    db.flush()

    # Secret *values* go to the secret store; only their names touch the row,
    # so nothing here can hand them back through the API.
    deployment.secret_keys = store_secrets(project.team_id, deployment.id, secrets)
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
    # Otherwise a deleted service's credentials would outlive it in the secret
    # store, with nothing left pointing at them.
    delete_secrets(project.team_id, deployment.id)
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