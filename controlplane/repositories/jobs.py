from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane.models import Finding, Job, Project, Scan
from controlplane.repositories.base import NotFoundError, Scope, paginate


class JobRepository:
    def __init__(self, session: Session, scope: Scope):
        self.session = session
        self.scope = scope

    def get(self, job_id: uuid.UUID) -> Job:
        job = self.session.get(Job, job_id)
        if job is None:
            raise NotFoundError()
        if job.project_id is not None:
            project = self.session.get(Project, job.project_id)
            if project is not None and not self.scope.can_access(project):
                raise NotFoundError()
        return job

    def create(self, project_id: uuid.UUID | None, type_: str) -> Job:
        job = Job(project_id=project_id, type=type_, status="queued")
        self.session.add(job)
        self.session.flush()
        return job

    def set_status(self, job: Job, status: str, error_message: str | None = None, celery_task_id: str | None = None) -> Job:
        job.status = status
        if error_message is not None:
            job.error_message = error_message
        if celery_task_id is not None:
            job.celery_task_id = celery_task_id
        self.session.flush()
        return job

    def append_log(self, job: Job, line: str) -> None:
        job.log = (job.log + line + "\n")[-200000:]
        self.session.flush()


class ScanRepository:
    def __init__(self, session: Session, scope: Scope):
        self.session = session
        self.scope = scope

    def _project(self, project_id: uuid.UUID) -> Project:
        project = self.session.get(Project, project_id)
        if project is None or not self.scope.can_access(project):
            raise NotFoundError()
        return project

    def create(self, project_id: uuid.UUID, tool: str, target: str, deployment_id: uuid.UUID | None = None) -> Scan:
        self.scope.guard(self._project(project_id), "scan.create")
        scan = Scan(
            project_id=project_id,
            deployment_id=deployment_id,
            tool=tool,
            target=target,
            status="queued",
        )
        self.session.add(scan)
        self.session.flush()
        return scan

    def get(self, scan_id: uuid.UUID) -> Scan:
        scan = self.session.get(Scan, scan_id)
        if scan is None:
            raise NotFoundError()
        self._project(scan.project_id)
        return scan

    def list(self, project_id: uuid.UUID, tool: str | None = None, page: int = 1, page_size: int = 20) -> tuple[list[Scan], int]:
        self._project(project_id)
        query = select(Scan).where(Scan.project_id == project_id)
        if tool:
            query = query.where(Scan.tool == tool)
        query = query.order_by(Scan.created_at.desc())
        return paginate(self.session, query, page, page_size)

    def list_all(self, project_id: uuid.UUID) -> list[Scan]:
        """Unpaged listing for aggregate endpoints (security summary)."""
        self._project(project_id)
        return list(
            self.session.scalars(
                select(Scan)
                .where(Scan.project_id == project_id)
                .order_by(Scan.created_at.desc())
            )
        )

    def set_result(self, scan: Scan, status: str, raw_output: dict | None = None, summary: dict | None = None, duration_seconds: int | None = None) -> Scan:
        scan.status = status
        if raw_output is not None:
            scan.raw_output = raw_output
        if summary is not None:
            scan.summary = summary
        if duration_seconds is not None:
            scan.duration_seconds = duration_seconds
        self.session.flush()
        return scan

    def add_findings(self, scan: Scan, findings: list[dict]) -> None:
        for item in findings:
            self.session.add(Finding(scan_id=scan.id, **item))
        self.session.flush()

    def list_findings(self, scan_id: uuid.UUID, severity: str | None = None, page: int = 1, page_size: int = 50) -> tuple[list[Finding], int]:
        self.get(scan_id)
        query = select(Finding).where(Finding.scan_id == scan_id)
        if severity:
            query = query.where(Finding.severity == severity)
        total = len(self.session.scalars(query).all())
        query = query.order_by(Finding.severity.asc()).offset((page - 1) * page_size).limit(page_size)
        return list(self.session.scalars(query)), total
