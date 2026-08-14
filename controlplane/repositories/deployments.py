import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane.models import Deployment, Project
from controlplane.repositories.base import NotFoundError, Scope, paginate


class DeploymentRepository:
    def __init__(self, session: Session, scope: Scope):
        self.session = session
        self.scope = scope

    def _project(self, project_id: uuid.UUID) -> Project:
        project = self.session.get(Project, project_id)
        if project is None or not self.scope.can_access(project):
            raise NotFoundError()
        return project

    def list(self, project_id: uuid.UUID, page: int = 1, page_size: int = 20) -> tuple[list[Deployment], int]:
        self._project(project_id)
        query = (
            select(Deployment)
            .where(Deployment.project_id == project_id)
            .order_by(Deployment.created_at.desc())
        )
        return paginate(self.session, query, page, page_size)

    def get(self, deployment_id: uuid.UUID) -> Deployment:
        deployment = self.session.get(Deployment, deployment_id)
        if deployment is None:
            raise NotFoundError()
        self._project(deployment.project_id)
        return deployment

    def create(
        self,
        project_id: uuid.UUID,
        service_name: str,
        repo_url: str,
        branch: str,
        port: int,
        replicas: int = 2,
        strategy: str = "deployment",
    ) -> Deployment:
        self.scope.guard(self._project(project_id), "deployment.create")
        deployment = Deployment(
            project_id=project_id,
            service_name=service_name,
            repo_url=repo_url,
            branch=branch,
            port=port,
            replicas=replicas,
            strategy=strategy,
            status="queued",
        )
        self.session.add(deployment)
        self.session.flush()
        return deployment

    def set_status(self, deployment: Deployment, status: str, image_ref: str | None = None, live_url: str | None = None) -> Deployment:
        deployment.status = status
        if image_ref is not None:
            deployment.image_ref = image_ref
        if live_url is not None:
            deployment.live_url = live_url
        self.session.flush()
        return deployment

    def delete(self, deployment_id: uuid.UUID) -> None:
        deployment = self.get(deployment_id)
        self.scope.guard(self._project(deployment.project_id), "deployment.delete")
        self.session.delete(deployment)
        self.session.flush()
