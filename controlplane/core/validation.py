"""Hard caps and validation helpers for InfraSpec (docs/PLATFORM_SPEC.md §6.2).

Caps are intentionally module-level constants so both the Pydantic validators
and the repository layer can reference the same source of truth.
"""

import uuid

from controlplane.core.config import settings

MAX_NODES_PER_PROJECT: int = settings.max_nodes_per_project
MAX_VCPU_PER_NODE: int = 8
MIN_VCPU_PER_NODE: int = 1
MAX_MEMORY_MB_PER_NODE: int = 16384
MIN_MEMORY_MB_PER_NODE: int = 1024
MAX_DISK_GB_PER_NODE: int = 200
MIN_DISK_GB_PER_NODE: int = 20
MAX_TOTAL_VCPU: int = settings.max_total_vcpu
MAX_TOTAL_MEMORY_MB: int = settings.max_total_memory_mb

PROJECT_NAME_RE = r"^[a-z0-9-]{3,30}$"
NODE_NAME_RE = r"^[a-z0-9-]{1,20}$"

ROLES = ("k8s_master", "k8s_worker", "docker_host")
KUBERNETES_VERSIONS = ("1.27", "1.28", "1.29")
CONTAINER_RUNTIMES = ("containerd", "crio")
CNI_PLUGINS = ("calico", "flannel")
DOCKER_VERSIONS = ("24.0", "25.0", "26.1")


def k8s_namespace(project_id: uuid.UUID) -> str:
    """The Kubernetes namespace a project's workloads live in — in namespace
    mode this is the literal shared-cluster namespace; in VM mode it is still
    used to name the app namespace inside that project's own cluster.

    Deliberately **not** derived from ``Project.name``: names are unique only
    per team (models/project.py — team_id is the isolation boundary, not a
    global one), so two different teams can each have a project literally
    named "staging". A namespace derived from that name would put both
    teams' workloads in the same namespace, and worse, teardown of one
    project would delete the other's (destroy_task issues
    ``kubectl delete namespace <name>``).

    ``project_id`` is the one thing that actually is globally unique and
    immutable, so this is safe to recompute anywhere rather than needing to
    be read from a stored column — there is no "the name changed under us"
    case to guard against.
    """
    return f"p-{project_id.hex[:20]}"
