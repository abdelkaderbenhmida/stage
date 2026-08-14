"""Pydantic v2 models for the InfraSpec document (§6.1) and every validation
rule in §6.2. All models use ``extra="forbid"`` so unknown keys are rejected.

Every validator raises ``ValueError`` with a message suitable for direct
display in the UI.
"""

import ipaddress
import re
from typing import Literal

from controlplane.core.validation import (
    MAX_DISK_GB_PER_NODE,
    MAX_MEMORY_MB_PER_NODE,
    MAX_NODES_PER_PROJECT,
    MAX_TOTAL_MEMORY_MB,
    MAX_TOTAL_VCPU,
    MAX_VCPU_PER_NODE,
    MIN_DISK_GB_PER_NODE,
    MIN_MEMORY_MB_PER_NODE,
    MIN_VCPU_PER_NODE,
    NODE_NAME_RE,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Role = Literal["k8s_master", "k8s_worker", "docker_host"]


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NODE_NAME_RE)
    vcpu: int
    memory_mb: int
    disk_gb: int
    role: Role

    @field_validator("name")
    @classmethod
    def name_is_safe(cls, value: str) -> str:
        if not re.fullmatch(NODE_NAME_RE, value):
            raise ValueError(
                f"Invalid node name {value!r}: only lowercase letters, digits and "
                "hyphens are allowed (1-20 chars)."
            )
        return value

    @field_validator("vcpu", "memory_mb", "disk_gb")
    @classmethod
    def _limits_human_readable(cls, value: int, info) -> int:
        bounds = {
            "vcpu": (MIN_VCPU_PER_NODE, MAX_VCPU_PER_NODE),
            "memory_mb": (MIN_MEMORY_MB_PER_NODE, MAX_MEMORY_MB_PER_NODE),
            "disk_gb": (MIN_DISK_GB_PER_NODE, MAX_DISK_GB_PER_NODE),
        }
        lo, hi = bounds[info.field_name]
        if not lo <= value <= hi:
            raise ValueError(f"{info.field_name} must be between {lo} and {hi} (got {value})")
        return value


class NetworkSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cidr: str
    domain: str = Field(pattern=r"^[a-z0-9.-]{1,253}$")

    @field_validator("cidr")
    @classmethod
    def cidr_is_rfc1918(cls, value: str) -> str:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise ValueError(f"{value!r} is not a valid IPv4 CIDR.") from exc
        if network.version != 4 or not network.is_private:
            raise ValueError(
                f"Network CIDR {value!r} must be RFC1918 private space (10/8, "
                "172.16/12, or 192.168/16)."
            )
        if not (16 <= network.prefixlen <= 28):
            raise ValueError(
                f"Network prefix /{network.prefixlen} is out of range: must be /16 to /28."
            )
        return value


class ConfigSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kubernetes_version: Literal["1.27", "1.28", "1.29"] = "1.28"
    container_runtime: Literal["containerd", "crio"] = "containerd"
    cni_plugin: Literal["calico", "flannel"] = "calico"
    docker_version: Literal["24.0", "25.0", "26.1"] = "24.0"


class InfraSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    project: str = Field(pattern=r"^[a-z0-9-]{3,30}$")
    network: NetworkSpec
    nodes: list[NodeSpec]
    config: ConfigSpec = ConfigSpec()

    # "namespace" carves a quota-bounded namespace out of a shared cluster in
    # seconds; "vm" provisions dedicated VMs via Terraform and takes minutes.
    # Namespace isolation is weaker — a container escape crosses it — so
    # anything sensitive should choose "vm".
    mode: Literal["namespace", "vm"] = "vm"

    # "light" ships only a scrape target and log shipper (~100 MB); "full"
    # deploys the whole per-environment monitoring stack, which on a small
    # node costs more than the workload it observes.
    observability: Literal["light", "full"] = "light"

    @field_validator("version")
    @classmethod
    def version_supported(cls, value: int) -> int:
        if value != 1:
            raise ValueError(f"Unsupported InfraSpec version {value}: only version 1 is supported.")
        return value

    @model_validator(mode="after")
    def enforce_limits(self) -> "InfraSpec":
        if not (1 <= len(self.nodes) <= MAX_NODES_PER_PROJECT):
            raise ValueError(
                f"Project must define between 1 and {MAX_NODES_PER_PROJECT} nodes; "
                f"got {len(self.nodes)}."
            )

        names = [node.name for node in self.nodes]
        if len(set(names)) != len(names):
            duplicates = sorted({name for name in names if names.count(name) > 1})
            raise ValueError(
                f"Node names must be unique within the project; duplicates: {duplicates}."
            )

        total_vcpu = sum(node.vcpu for node in self.nodes)
        if total_vcpu > MAX_TOTAL_VCPU:
            raise ValueError(
                f"Total vCPU across the project is {total_vcpu}, exceeding the "
                f"cap of {MAX_TOTAL_VCPU}."
            )

        total_memory = sum(node.memory_mb for node in self.nodes)
        if total_memory > MAX_TOTAL_MEMORY_MB:
            raise ValueError(
                f"Total memory across the project is {total_memory} MB, exceeding "
                f"the cap of {MAX_TOTAL_MEMORY_MB} MB."
            )

        masters = [n for n in self.nodes if n.role == "k8s_master"]
        workers = [n for n in self.nodes if n.role == "k8s_worker"]
        docker_only = all(n.role == "docker_host" for n in self.nodes)

        if workers and len(masters) != 1:
            raise ValueError(
                "A Kubernetes cluster must have exactly one master node when "
                f"workers are present (found {len(masters)} masters)."
            )
        if not workers and not docker_only and len(masters) != 1:
            raise ValueError(
                "Exactly one k8s_master is required unless the project is "
                "all docker_host nodes."
            )
        return self


class SpecValidationError(ValueError):
    """Wraps a Pydantic ValidationError into a single human-readable message."""

    def __init__(self, exc: Exception):
        self.exc = exc
        messages = []
        errors = getattr(exc, "errors", lambda: [])()
        for err in errors:
            loc = ".".join(str(part) for part in err.get("loc", ()))
            msg = err.get("msg", str(err))
            messages.append(f"{loc}: {msg}" if loc else msg)
        super().__init__("; ".join(messages))
