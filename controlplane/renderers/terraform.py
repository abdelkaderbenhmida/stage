"""Render an InfraSpec into a self-contained Terraform workspace.

The rendered ``main.tf`` generalizes the fixed master-plus-``worker_count``
model of ``terraform/main.tf`` into an arbitrary validated node list. IPs are
assigned deterministically (host offset 10 upward, in node-list order) so the
same spec always renders byte-identically.
"""

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from controlplane.schemas.spec import InfraSpec

_TEMPLATE_DIR = Path(__file__).parent / "templates" / "terraform"

# Files that ship verbatim (they use Terraform's own templatefile syntax).
_STATIC_FILES = ("cloud-init.tpl", "network-config.tpl")


@dataclass
class TerraformRuntimeConfig:
    """Host-side values injected by the control plane, not by the user."""

    libvirt_uri: str = "qemu:///system"
    storage_pool: str = "default"
    base_image_path: str = "/var/lib/libvirt/images/ubuntu-22.04-server-cloudimg-amd64.img"
    dns_servers: list = field(default_factory=lambda: ["1.1.1.1", "8.8.8.8"])
    network_interface: str = "enp1s0"
    ssh_user: str = "devops"
    ssh_public_key: str = ""
    libvirt_volume_owner_uid: int = 64055
    libvirt_volume_group_gid: int = 993
    gateway_ip: str | None = None

    # Remote state (§7 item 1): when state_url is set, every rendered
    # workspace gets a per-project `backend "http"` block, so state survives
    # container restarts instead of being orphaned with the workspace.
    # TLS terminates at the state server; `insecure` exists for the
    # self-signed homelab case and must stay false in prod.
    state_url: str = ""
    state_username: str = ""
    state_password: str = ""
    state_insecure: bool = False


def _assign_ips(spec: InfraSpec) -> list[dict]:
    network = ipaddress.ip_network(spec.network.cidr, strict=True)
    nodes = []
    for index, node in enumerate(spec.nodes):
        nodes.append(
            {
                "name": node.name,
                "ip": str(network.network_address + (10 + index)),
                "vcpu": node.vcpu,
                "memory_mb": node.memory_mb,
                "disk_gb": node.disk_gb,
                "role": node.role,
            }
        )
    return nodes


def _hcl_heredoc(value: str) -> str:
    """Render a multi-line scalar as a safe HCL heredoc."""
    return f"<<-EOT\n{value}\nEOT"


def _hcl_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _hcl_attr_lines(attrs: dict, indent: str = "") -> str:
    """Render ``key = value`` lines with `=` aligned to terraform fmt rules."""
    if not attrs:
        return ""
    width = max(len(key) for key in attrs)
    return "\n".join(
        f"{indent}{key.ljust(width)} = {_hcl_scalar_or_block(value)}"
        for key, value in attrs.items()
    )


def _hcl_scalar_or_block(value):
    if isinstance(value, list):
        inner = ", ".join(_hcl_scalar(item) for item in value)
        return f"[{inner}]"
    if isinstance(value, dict):
        inner = _hcl_attr_lines(value, indent="  ")
        return f"{{\n{inner}\n}}"
    if isinstance(value, str) and "\n" in value:
        return value  # already-rendered heredoc or block, emit verbatim
    return _hcl_scalar(value)


def _render_tfvars(spec: InfraSpec, runtime: TerraformRuntimeConfig) -> str:
    """Generate fmt-clean terraform.tfvars.

    terraform fmt aligns `=` within a contiguous group of scalar attributes
    and renders multi-line (collection/block) values in their own group, so
    the two groups are emitted separately.
    """
    network = ipaddress.ip_network(spec.network.cidr, strict=True)
    node_blocks = []
    for node in _assign_ips(spec):
        node_blocks.append(f"  {{\n{_hcl_attr_lines(node, indent='    ')}\n  }}")
    scalars = {
        "libvirt_uri": runtime.libvirt_uri,
        "storage_pool": runtime.storage_pool,
        "base_image_name": f"{spec.project}-base.qcow2",
        "base_image_path": runtime.base_image_path,
        "network_name": f"{spec.project}-net",
        "network_domain": spec.network.domain,
        "network_cidr": spec.network.cidr,
        "network_prefix": network.prefixlen,
        "gateway_ip": runtime.gateway_ip or str(network.network_address + 1),
        "dns_servers": runtime.dns_servers,
        "network_interface": runtime.network_interface,
        "ssh_user": runtime.ssh_user,
        "ssh_public_key": _hcl_heredoc(runtime.ssh_public_key),
        "libvirt_volume_owner_uid": runtime.libvirt_volume_owner_uid,
        "libvirt_volume_group_gid": runtime.libvirt_volume_group_gid,
    }
    width = max(len(key) for key in scalars)
    scalar_lines = [
        f"{key.ljust(width)} = {_hcl_scalar_or_block(value)}"
        for key, value in scalars.items()
    ]
    nodes_line = "nodes = [\n" + ",\n".join(node_blocks) + "\n]"
    return "\n".join(scalar_lines) + "\n" + nodes_line + "\n"


def _hcl_escape(value) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _state_backend_block(runtime: TerraformRuntimeConfig, spec: InfraSpec) -> str:
    """Per-project HTTP backend inside the terraform block, or '' when the
    operator has not configured a state server (dev)."""
    if not runtime.state_url:
        return ""
    attrs: dict[str, str] = {
        "address": f'"{_hcl_escape(runtime.state_url.rstrip("/"))}/{_hcl_escape(spec.project)}.tfstate"',
    }
    if runtime.state_username:
        attrs["username"] = f'"{_hcl_escape(runtime.state_username)}"'
    if runtime.state_password:
        attrs["password"] = f'"{_hcl_escape(runtime.state_password)}"'
    attrs["update_method"] = '"POST"'
    if runtime.state_insecure:
        attrs["insecure"] = "true"
    width = max(len(key) for key in attrs)
    lines = ['backend "http" {']
    lines += [f"  {key.ljust(width)} = {value}" for key, value in attrs.items()]
    lines.append("}")
    return "\n".join(lines)


def render_terraform(spec: InfraSpec, runtime: TerraformRuntimeConfig, workspace: Path) -> list[Path]:
    """Render all Terraform artifacts into ``workspace`` and return written paths."""
    workspace.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    written = []
    backend_block = _state_backend_block(runtime, spec)
    for name in ("main.tf", "variables.tf", "outputs.tf"):
        template = env.get_template(f"{name}.j2")
        (workspace / name).write_text(template.render(backend_block=backend_block))
        written.append(workspace / name)

    (workspace / "terraform.tfvars").write_text(_render_tfvars(spec, runtime))
    written.append(workspace / "terraform.tfvars")

    for name in _STATIC_FILES:
        dest = workspace / name
        dest.write_text((_TEMPLATE_DIR / name).read_text())
        written.append(dest)

    return written
