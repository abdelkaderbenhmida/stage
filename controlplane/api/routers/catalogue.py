"""Service catalogue (docs/TODO.md Task 5.3).

One page answering "what is running, who owns it, and is it safe" across every
team the caller belongs to. This is the view that makes a platform feel like a
product rather than a collection of tools.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from controlplane import platform_ops
from controlplane.api.deps import get_current_user
from controlplane.api.schemas import CatalogueEntry
from controlplane.db import get_db
from controlplane.models import Deployment, Finding, Job, Project, Scan, User
from controlplane.repositories.base import Scope

router = APIRouter(tags=["catalogue"])


@router.get("/catalogue", response_model=list[CatalogueEntry])
def catalogue(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    status_filter: str | None = Query(default=None, alias="status"),
    severity: str | None = Query(default=None),
):
    scope = Scope.from_session(db, user.id)
    if not scope.team_ids and not scope.is_system:
        return []

    # Scope to the caller's teams, falling back to direct ownership for
    # projects created before teams existed. Same rule as the repository layer.
    project_filter = scope.project_filter()

    rows = db.execute(
        select(Deployment, Project)
        .join(Project, Project.id == Deployment.project_id)
        .where(project_filter)
        .order_by(Deployment.updated_at.desc())
    ).all()

    # One aggregate query for severity counts rather than per-deployment
    # queries, which would be N+1 across the whole catalogue.
    counts = {
        (project_id, severity_value): total
        for project_id, severity_value, total in db.execute(
            select(Scan.project_id, Finding.severity, func.count(Finding.id))
            .join(Finding, Finding.scan_id == Scan.id)
            .group_by(Scan.project_id, Finding.severity)
        ).all()
    }

    # Latest job per deployment, for the "logs" link (Task 5.3 step 3).
    latest_job = {
        deployment_id: job_id
        for deployment_id, job_id in db.execute(
            select(Job.deployment_id, Job.id)
            .distinct(Job.deployment_id)
            .order_by(Job.deployment_id, Job.created_at.desc())
        ).all()
    }

    # Owner email per project, so the catalogue shows who to talk to.
    owners = {
        project_id: email
        for project_id, email in db.execute(
            select(Project.id, User.email).join(User, User.id == Project.owner_id)
        ).all()
    }

    entries = []
    for deployment, project in rows:
        if status_filter and deployment.status != status_filter:
            continue
        critical = counts.get((project.id, "critical"), 0)
        high = counts.get((project.id, "high"), 0)
        if severity == "critical" and critical == 0:
            continue
        if severity == "high" and high == 0:
            continue
        entries.append(
            CatalogueEntry(
                deployment_id=deployment.id,
                service_name=deployment.service_name,
                project_id=project.id,
                project_name=project.name,
                team_id=project.team_id,
                status=deployment.status,
                live_url=deployment.live_url,
                branch=deployment.branch,
                updated_at=deployment.updated_at,
                critical=critical,
                high=high,
                logs_job_id=latest_job.get(deployment.id),
                owner_email=owners.get(project.id),
            )
        )
    return entries


@router.get("/my/apps")
def my_apps(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every service the caller owns, with what the cluster says about it.

    The operator console answers "how is the platform" — its own repository,
    its own services, its namespace, its Vault. A tenant needs the same shape
    of answer about *their* apps, and until now had to open one project at a
    time to get it. This is that page: one row per deployment, across every
    project the caller can see, with the live pod count read from that
    project's own namespace.

    Namespaces are derived from the project id server-side, exactly as
    /projects/{id}/workloads does, so this cannot be pointed at a namespace
    the caller does not own.
    """
    from controlplane.core.validation import k8s_namespace

    scope = Scope.from_session(db, user.id)
    if not scope.team_ids and not scope.is_system:
        return {"projects": 0, "apps": []}

    rows = db.execute(
        select(Deployment, Project)
        .join(Project, Project.id == Deployment.project_id)
        .where(scope.project_filter())
        .order_by(Project.name, Deployment.service_name)
    ).all()

    latest_job = {
        deployment_id: (job_id, status)
        for deployment_id, job_id, status in db.execute(
            select(Job.deployment_id, Job.id, Job.status)
            .distinct(Job.deployment_id)
            .order_by(Job.deployment_id, Job.created_at.desc())
        ).all()
    }

    # One kubectl call per namespace, not per deployment: a project with six
    # services would otherwise shell out six times for the same answer.
    pods_by_namespace: dict[str, list] = {}
    apps = []
    for deployment, project in rows:
        namespace = k8s_namespace(project.id)
        if namespace not in pods_by_namespace:
            pods_by_namespace[namespace] = (
                platform_ops.pods_status(namespace).get("pods", []) or []
            )
        pods = [p for p in pods_by_namespace[namespace] if deployment.service_name in (p.get("name") or "")]
        job_id, job_status = latest_job.get(deployment.id, (None, None))
        apps.append({
            "deployment_id": str(deployment.id),
            "service_name": deployment.service_name,
            "project_id": str(project.id),
            "project_name": project.name,
            "project_status": project.status,
            "branch": deployment.branch,
            "status": deployment.status,
            "live_url": deployment.live_url,
            "image_ref": deployment.image_ref,
            "replicas": deployment.replicas,
            "pods_ready": sum(1 for p in pods if p.get("ready")),
            "pods_total": len(pods),
            "last_job_id": str(job_id) if job_id else None,
            "last_job_status": job_status,
            "updated_at": deployment.updated_at,
        })

    return {"projects": len({a["project_id"] for a in apps}), "apps": apps}
