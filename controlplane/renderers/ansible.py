"""Render an InfraSpec into an Ansible inventory and group_vars.

Groups are derived from node roles (``masters``, ``workers``,
``docker_hosts``). The existing roles in ``ansible/roles/`` are reused
unchanged; only the enum-constrained ``config`` values reach group_vars.
"""

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from controlplane.schemas.spec import InfraSpec

_TEMPLATE_DIR = Path(__file__).parent / "templates" / "ansible"

_ROLE_TO_GROUP = {
    "k8s_master": "masters",
    "k8s_worker": "workers",
    "docker_host": "docker_hosts",
}

# container_runtime -> (socket, cri_endpoint)
_RUNTIME_SOCKETS = {
    "containerd": ("/run/containerd/containerd.sock", "unix:///run/containerd/containerd.sock"),
    "crio": ("/run/crio/crio.sock", "unix:///run/crio/crio.sock"),
}

_CNI_VERSIONS = {"calico": "v3.26.1", "flannel": None}


@dataclass
class AnsibleRuntimeConfig:
    """Host-side values injected by the control plane."""

    ssh_user: str = "devops"
    pod_cidr: str = "192.168.0.0/16"
    service_cidr: str = "10.96.0.0/12"


def _grouped_nodes(spec: InfraSpec) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for node in spec.nodes:
        group = _ROLE_TO_GROUP[node.role]
        groups.setdefault(group, []).append(
            {"name": node.name, "ip": _ip_of(spec, node.name), "role": node.role}
        )
    return groups


def _ip_of(spec: InfraSpec, node_name: str) -> str:
    """Mirror the deterministic IP assignment of the Terraform renderer."""
    import ipaddress

    network = ipaddress.ip_network(spec.network.cidr, strict=True)
    for index, node in enumerate(spec.nodes):
        if node.name == node_name:
            return str(network.network_address + (10 + index))
    raise KeyError(node_name)


def _config_vars(spec: InfraSpec, runtime: AnsibleRuntimeConfig) -> dict:
    socket, endpoint = _RUNTIME_SOCKETS[spec.config.container_runtime]
    return {
        "kubernetes_version": spec.config.kubernetes_version,
        "cni_plugin": spec.config.cni_plugin,
        "calico_version": _CNI_VERSIONS[spec.config.cni_plugin],
        "pod_cidr": runtime.pod_cidr,
        "service_cidr": runtime.service_cidr,
        "containerd_socket": socket,
        "cri_endpoint": endpoint,
        "docker_version": spec.config.docker_version,
    }


def render_ansible(spec: InfraSpec, runtime: AnsibleRuntimeConfig, workspace: Path) -> list[Path]:
    workspace.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    groups = _grouped_nodes(spec)
    inventory_path = workspace / "inventory.ini"
    inventory_path.write_text(
        env.get_template("inventory.ini.j2").render(
            groups=groups, ssh_user=runtime.ssh_user
        )
    )

    group_vars_dir = workspace / "group_vars"
    group_vars_dir.mkdir(parents=True, exist_ok=True)
    vars_path = group_vars_dir / "all.yml"
    vars_path.write_text(
        env.get_template("group_vars/all.yml.j2").render(config=_config_vars(spec, runtime))
    )

    return [inventory_path, vars_path]
