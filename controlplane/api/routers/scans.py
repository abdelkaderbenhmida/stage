"""Security scans and reporting (docs/PLATFORM_SPEC.md §8 Scans)."""

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from controlplane.api.deps import audit, get_current_user, get_db, get_scope, pagination_headers
from controlplane.api.rate_limit import check_rate_limit
from controlplane.api.rbac import require_project_action
from controlplane.api.schemas import (
    FindingsPage,
    ScanOut,
    ScanRequest,
    SecuritySummaryOut,
)
from controlplane.core.config import settings
from controlplane.models import Project, User
from controlplane.repositories.base import NotFoundError, Scope
from controlplane.repositories.jobs import ScanRepository
from controlplane.repositories.projects import ProjectRepository
from controlplane.workers import tasks

router = APIRouter(tags=["scans"])


def _require_project(db: Session, scope: Scope, project_id: uuid.UUID):
    try:
        return ProjectRepository(db, scope).get_project(project_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.") from None


_TOOLS = ("trivy", "gitleaks", "pip_audit")


@router.post("/projects/{project_id}/scans", response_model=list[ScanOut], status_code=status.HTTP_202_ACCEPTED)
def create_scans(
    body: ScanRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
    project: Project = Depends(require_project_action("scan.create")),
):
    if not check_rate_limit(f"scans:{user.id}", settings.scans_per_hour, 3600):
        raise HTTPException(status_code=429, detail="Scan limit reached for this hour.") from None

    tools = list(_TOOLS) if body.tool == "all" else [body.tool]
    repo = ScanRepository(db, scope)
    created = [repo.create(project.id, tool, body.target) for tool in tools]

    # Commit before dispatching, never inside the loop. scan_task looks the
    # Scan row up by id and returns early when it is missing, so handing the
    # id to a worker while the row is still uncommitted is a race the worker
    # can win: it finds nothing, returns, and leaves the scan stuck "queued"
    # with its job stuck "running" forever. Observed exactly that — three
    # scans queued, the fastest task returned in 6ms having done nothing.
    db.commit()

    for scan in created:
        tasks.queue_scan(scan, project.id)
    audit(db, user.id, "scans.create", request, resource_type="project", resource_id=str(project.id),
          detail={"tools": tools, "target": body.target}, team_id=project.team_id)
    return created


@router.get("/projects/{project_id}/scans", response_model=list[ScanOut])
def list_scans(
    project_id: uuid.UUID,
    request: Request,
    response: Response,
    tool: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    _require_project(db, scope, project_id)
    items, total = ScanRepository(db, scope).list(project_id, tool=tool, page=page, page_size=page_size)
    response.headers.update(pagination_headers(request, total, page, page_size))
    return items


@router.get("/scans/{scan_id}", response_model=ScanOut)
def get_scan(
    scan_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    try:
        return ScanRepository(db, scope).get(scan_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Scan not found.") from None


@router.get("/scans/{scan_id}/findings", response_model=FindingsPage)
def get_findings(
    scan_id: uuid.UUID,
    severity: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    try:
        findings, total = ScanRepository(db, scope).list_findings(scan_id, severity, page, page_size)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Scan not found.") from None
    return FindingsPage(items=findings, total=total, page=page)


@router.get("/projects/{project_id}/security/summary", response_model=SecuritySummaryOut)
def security_summary(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    _require_project(db, scope, project_id)
    repo = ScanRepository(db, scope)
    scans = repo.list_all(project_id)

    current = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    latest_by_tool: dict[str, object] = {}
    # Most recent scan per tool regardless of outcome, so a tool whose latest
    # run failed can be reported as such rather than silently contributing
    # nothing to `current` and reading as clean.
    latest_attempt_by_tool: dict[str, object] = {}
    # `list_all` returns newest first, so the FIRST scan seen for a tool is
    # its latest. Assigning unconditionally would keep overwriting until the
    # oldest one won, which is how "current findings" came to report the
    # first scan a project ever ran instead of the most recent.
    for scan in scans:
        latest_attempt_by_tool.setdefault(scan.tool, scan)
        if scan.status == "completed" and scan.summary:
            latest_by_tool.setdefault(scan.tool, scan)
    for scan in latest_by_tool.values():
        for key in current:
            current[key] += (scan.summary or {}).get(key, 0)
    failed_tools = sorted(
        tool for tool, scan in latest_attempt_by_tool.items() if scan.status != "completed"
    )

    now = datetime.now(UTC)
    start = now - timedelta(days=30)
    trend_by_day: dict[str, Counter] = defaultdict(Counter)
    for scan in scans:
        if scan.created_at and scan.created_at >= start and scan.summary:
            day = scan.created_at.date().isoformat()
            trend_by_day[day]["critical"] += scan.summary.get("critical", 0)
            trend_by_day[day]["high"] += scan.summary.get("high", 0)
    trend = [{"date": day, **counter} for day, counter in sorted(trend_by_day.items())]

    issue_counter = Counter()
    issue_meta: dict[str, dict] = {}
    for scan in scans:
        for finding in scan.findings:
            key = finding.identifier or finding.title or finding.package_name or "unknown"
            issue_counter[key] += 1
            issue_meta.setdefault(key, {
                "identifier": finding.identifier,
                "severity": finding.severity,
                "package_name": finding.package_name,
                "fixed_version": finding.fixed_version,
                "title": finding.title,
            })
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
    top = sorted(
        ({"count": count, **issue_meta[key]} for key, count in issue_counter.items()),
        key=lambda item: (severity_rank.get(item["severity"], 9), -item["count"]),
    )[:10]

    return SecuritySummaryOut(
        project_id=project_id,
        current=current,
        trend=trend,
        top_issues=top,
        failed_tools=failed_tools,
    )
