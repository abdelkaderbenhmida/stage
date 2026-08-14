"""Hard caps and validation helpers for InfraSpec (docs/PLATFORM_SPEC.md §6.2).

Caps are intentionally module-level constants so both the Pydantic validators
and the repository layer can reference the same source of truth.
"""

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
