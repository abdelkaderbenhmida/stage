"""Cost estimation (docs/TODO.md Task 5.1).

Cost is derived from allocated resources multiplied by how long the project
has been running, rather than measured consumption. That is deliberate: the
platform bills the *reservation*, because a reserved vCPU is unavailable to
anyone else whether or not it is busy.

These are estimates for visibility and for justifying the TTL reaper — they
are not an invoice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from controlplane.core.config import settings
from controlplane.models import Project


@dataclass(frozen=True)
class CostBreakdown:
    project_id: str
    project_name: str
    vcpu: int
    memory_gb: float
    disk_gb: int
    hours: float
    vcpu_cost: float
    memory_cost: float
    disk_cost: float
    total: float
    currency: str

    def as_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "vcpu": self.vcpu,
            "memory_gb": round(self.memory_gb, 2),
            "disk_gb": self.disk_gb,
            "hours": round(self.hours, 2),
            "vcpu_cost": round(self.vcpu_cost, 4),
            "memory_cost": round(self.memory_cost, 4),
            "disk_cost": round(self.disk_cost, 4),
            "total": round(self.total, 2),
            "currency": self.currency,
        }


def _allocated(project: Project) -> tuple[int, float, int]:
    nodes = (project.infra_spec or {}).get("nodes", [])
    vcpu = sum(node.get("vcpu", 0) for node in nodes)
    memory_gb = sum(node.get("memory_mb", 0) for node in nodes) / 1024
    disk_gb = sum(node.get("disk_gb", 0) for node in nodes)
    return vcpu, memory_gb, disk_gb


def billable_hours(project: Project, now: datetime | None = None) -> float:
    """Hours a project has held its allocation.

    The clock starts when the project was created and stops when it was
    destroyed; a destroyed project must stop accruing cost, otherwise last
    month's environments keep inflating this month's total.
    """
    now = now or datetime.now(UTC)
    start = project.created_at or now
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)

    if project.status in ("destroyed", "draft"):
        end = project.updated_at or start
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
    else:
        end = now

    return max(0.0, (end - start).total_seconds() / 3600)


def estimate(project: Project, now: datetime | None = None) -> CostBreakdown:
    vcpu, memory_gb, disk_gb = _allocated(project)
    hours = billable_hours(project, now)

    vcpu_cost = vcpu * settings.cost_per_vcpu_hour * hours
    memory_cost = memory_gb * settings.cost_per_gb_ram_hour * hours
    disk_cost = disk_gb * settings.cost_per_gb_disk_hour * hours

    return CostBreakdown(
        project_id=str(project.id),
        project_name=project.name,
        vcpu=vcpu,
        memory_gb=memory_gb,
        disk_gb=disk_gb,
        hours=hours,
        vcpu_cost=vcpu_cost,
        memory_cost=memory_cost,
        disk_cost=disk_cost,
        total=vcpu_cost + memory_cost + disk_cost,
        currency=settings.cost_currency,
    )


def summarise(projects: list[Project], now: datetime | None = None) -> dict:
    breakdowns = [estimate(project, now) for project in projects]
    return {
        "currency": settings.cost_currency,
        "total": round(sum(b.total for b in breakdowns), 2),
        "projects": [b.as_dict() for b in breakdowns],
    }
