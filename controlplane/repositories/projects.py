import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from controlplane.models import Job, Node, Project
from controlplane.repositories.base import NotFoundError, Scope, paginate


class ProjectRepository:
    """Project queries scoped to the caller's team memberships (Task 3.1).

    The scope argument is mandatory: a repository built without one is a
    compile-time error, not a silent leak.
    """

    def __init__(self, session: Session, scope: Scope):
        self.session = session
        self.scope = scope

    def _get(self, project_id: uuid.UUID) -> Project:
        project = self.session.get(Project, project_id)
        if project is None or not self.scope.can_access(project):
            raise NotFoundError()
        return project

    def list_projects(self, page: int = 1, page_size: int = 20) -> tuple[list[Project], int]:
        query = (
            select(Project)
            .where(self.scope.project_filter())
            .order_by(Project.created_at.desc())
        )
        return paginate(self.session, query, page, page_size)

    def get_project(self, project_id: uuid.UUID) -> Project:
        return self._get(project_id)

    def get_by_name(self, name: str) -> Project | None:
        return self.session.scalar(
            select(Project).where(
                self.scope.project_filter(), Project.name == name
            )
        )

    def count(self) -> int:
        return len(
            self.session.scalars(
                select(Project).where(self.scope.project_filter())
            ).all()
        )

    def create(
        self,
        name: str,
        infra_spec: dict,
        team_id: uuid.UUID,
        description: str | None = None,
    ) -> Project:
        if self.scope.is_system:
            raise RuntimeError("Cannot create a project with a system scope.")
        project = Project(
            owner_id=self.scope.user_id,
            team_id=team_id,
            name=name,
            description=description,
            infra_spec=infra_spec,
            status="draft",
        )
        self.session.add(project)
        self.session.flush()
        return project

    def update_spec(self, project: Project, infra_spec: dict, description: str | None = None) -> Project:
        self.scope.guard(project, "project.update")
        project.infra_spec = infra_spec
        if description is not None:
            project.description = description
        self.session.flush()
        return project

    def set_status(self, project: Project, status: str) -> Project:
        project.status = status
        self.session.flush()
        return project

    def delete(self, project: Project) -> None:
        self.scope.guard(project, "project.destroy")
        self.session.delete(project)
        self.session.flush()

    def nodes(self, project_id: uuid.UUID) -> list[Node]:
        self._get(project_id)
        return list(
            self.session.scalars(
                select(Node).where(Node.project_id == project_id).order_by(Node.created_at)
            )
        )

    def create_nodes(self, project_id: uuid.UUID, nodes: list[dict]) -> list[Node]:
        self._get(project_id)
        created = []
        for node in nodes:
            row = Node(
                project_id=project_id,
                name=node["name"],
                vcpu=node["vcpu"],
                memory_mb=node["memory_mb"],
                disk_gb=node["disk_gb"],
                role=node["role"],
            )
            self.session.add(row)
            created.append(row)
        self.session.flush()
        return created

    def replace_nodes(self, project_id: uuid.UUID, nodes: list[dict]) -> None:
        existing = self.nodes(project_id)
        for node in existing:
            self.session.delete(node)
        self.session.flush()
        self.create_nodes(project_id, nodes)

    def update_node_ip(self, project_id: uuid.UUID, node_name: str, ip: str) -> None:
        node = self.session.scalar(
            select(Node).where(Node.project_id == project_id, Node.name == node_name)
        )
        if node:
            node.ip_address = ip
            node.status = "running"
            self.session.flush()

    def get_active_provision_job(self, project_id: uuid.UUID) -> Job | None:
        return self.session.scalar(
            select(Job).where(
                Job.project_id == project_id,
                Job.type.in_(["provision", "destroy", "configure"]),
                Job.status.in_(["queued", "running"]),
            )
        )
