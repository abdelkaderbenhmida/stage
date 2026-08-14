"""Renderer acceptance: deterministic output, injection safety, and — when the
CLI tools are installed — terraform fmt/validate and ansible-inventory checks."""

import ipaddress
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from controlplane.renderers.ansible import AnsibleRuntimeConfig, render_ansible
from controlplane.renderers.terraform import TerraformRuntimeConfig, render_terraform
from controlplane.schemas.spec import InfraSpec
from pydantic import ValidationError

FIXTURE = {
    "version": 1,
    "project": "my-cluster",
    "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
    "config": {
        "kubernetes_version": "1.28",
        "container_runtime": "containerd",
        "cni_plugin": "calico",
    },
    "nodes": [
        {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
        {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
        {"name": "worker-2", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
        {"name": "edge", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "docker_host"},
    ],
}

RUNTIME = TerraformRuntimeConfig(
    libvirt_uri="qemu:///system",
    storage_pool="default",
    base_image_path="/var/lib/libvirt/images/ubuntu-22.04-server-cloudimg-amd64.img",
    ssh_user="devops",
    ssh_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGOLDENTEST user@host",
)


def _spec() -> InfraSpec:
    return InfraSpec.model_validate(FIXTURE)


def _render(tmp_path: Path):
    ws = tmp_path / "ws"
    render_terraform(_spec(), RUNTIME, ws)
    render_ansible(_spec(), AnsibleRuntimeConfig(), ws)
    return ws


def test_terraform_render_is_deterministic(tmp_path):
    ws = _render(tmp_path)
    files = sorted(p.name for p in ws.iterdir())
    assert "main.tf" in files and "terraform.tfvars" in files
    assert "variables.tf" in files and "outputs.tf" in files
    assert "cloud-init.tpl" in files and "network-config.tpl" in files
    assert "inventory.ini" in files and (ws / "group_vars/all.yml").exists()
    contents = {name: (ws / name).read_text() for name in files if (ws / name).is_file()}
    assert contents == {name: (ws / name).read_text() for name in files if (ws / name).is_file()}


def test_ip_assignment_is_deterministic():
    net = ipaddress.ip_network(FIXTURE["network"]["cidr"], strict=True)
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        render_terraform(_spec(), RUNTIME, ws)
        text = (ws / "terraform.tfvars").read_text()
        for offset in (10, 11, 12):
            assert f"{net.network_address + offset}" in text


def test_node_ips_in_tfvars_and_main_tf_wiring(tmp_path):
    ws = _render(tmp_path)
    tfvars = (ws / "terraform.tfvars").read_text()
    main = (ws / "main.tf").read_text()
    for offset in (10, 11, 12):
        ip = str(ipaddress.ip_network(FIXTURE["network"]["cidr"], strict=True).network_address + offset)
        assert ip in tfvars
    # main.tf must consume the per-node IPs from tfvars, never hardcode them.
    assert "each.value.ip" in main
    assert "each.key" in main
    for name in ("master", "worker-1", "worker-2"):
        assert name in tfvars


def test_rendered_tfvars_passes_terraform_fmt(tmp_path):
    terraform = shutil.which("terraform")
    if terraform is None:
        pytest.skip("terraform CLI not installed")
    ws = _render(tmp_path)
    result = subprocess.run(
        ["terraform", "fmt", "-check", "-diff", "."], cwd=ws, capture_output=True, text=True
    )
    assert result.returncode == 0, f"fmt diff:\n{result.stdout}\n{result.stderr}"


@pytest.mark.network
def test_terraform_validate(tmp_path):
    """Full `terraform init && validate` against the rendered workspace.

    Marked `network` and deselected by default: `terraform init` downloads the
    dmacvicar/libvirt provider from GitHub, so this fails on a slow or offline
    connection (observed: "net/http: TLS handshake timeout", passing on retry).
    A test that fails randomly teaches people to ignore failures, so the
    offline `terraform fmt -check` above is what guards every run; this one
    runs in CI, where the network is reliable.
    """
    terraform = shutil.which("terraform")
    if terraform is None:
        pytest.skip("terraform CLI not installed")
    ws = _render(tmp_path)
    init = subprocess.run(
        ["terraform", "init", "-backend=false", "-input=false"], cwd=ws, capture_output=True, text=True
    )
    assert init.returncode == 0, init.stderr
    validate = subprocess.run(
        ["terraform", "validate"], cwd=ws, capture_output=True, text=True
    )
    assert validate.returncode == 0, f"{validate.stdout}\n{validate.stderr}"


def test_ansible_inventory_parses(tmp_path):
    ansible_inventory = shutil.which("ansible-inventory")
    if ansible_inventory is None:
        pytest.skip("ansible-inventory not installed")
    ws = _render(tmp_path)
    result = subprocess.run(
        [ansible_inventory, "-i", "inventory.ini", "--list"],
        cwd=ws, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "masters" in result.stdout and "workers" in result.stdout and "docker_hosts" in result.stdout


@pytest.mark.security
def test_hostile_spec_values_never_break_out(tmp_path):
    hostile = dict(FIXTURE)
    hostile["project"] = "evil; rm -rf /"
    # Assert the specific failure: a blind `Exception` would also pass if the
    # spec blew up for an unrelated reason, hiding a regression in the very
    # validation this test exists to prove.
    with pytest.raises(ValidationError):
        InfraSpec.model_validate(hostile)
    # A spec that survives validation must still not inject raw shell/HCL.
    # Craft a spec whose valid-but-weird values are exercised end to end.
    weird = dict(FIXTURE)
    weird["network"] = {"cidr": "10.100.0.0/24", "domain": "a-b.dev.local"}
    parsed = InfraSpec.model_validate(weird)
    ws = tmp_path / "ws"
    render_terraform(parsed, RUNTIME, ws)
    text = (ws / "terraform.tfvars").read_text()
    assert '";' not in text
    assert "$(" not in text
    assert "`" not in text
    assert "{evil}" not in text


# --- §7 item 1: per-project remote state backend ---------------------------------


def test_remote_state_backend_absent_by_default(tmp_path):
    ws = _render(tmp_path)
    main = (ws / "main.tf").read_text()
    assert 'backend "http"' not in main


def test_remote_state_backend_rendered_when_configured(tmp_path):
    runtime = TerraformRuntimeConfig(
        libvirt_uri="qemu:///system",
        state_url="https://state.devops.local/terraform",
        state_username="cp-worker",
        state_password="s3cr3t",
        state_insecure=False,
    )
    ws = tmp_path / "ws"
    render_terraform(_spec(), runtime, ws)
    main = (ws / "main.tf").read_text()

    assert 'backend "http"' in main
    assert '"https://state.devops.local/terraform/my-cluster.tfstate"' in main
    assert '"cp-worker"' in main
    assert '"s3cr3t"' in main
    assert 'update_method = "POST"' in main
    assert "insecure" not in main
    # backends appear inside the terraform block
    block = main.split('backend "http"')[1].split("}")[0]
    assert "required_version" not in block


def test_remote_state_insecure_flag_rendered(tmp_path):
    runtime = TerraformRuntimeConfig(
        libvirt_uri="qemu:///system",
        state_url="https://state.devops.local",
        state_insecure=True,
    )
    ws = tmp_path / "ws"
    render_terraform(_spec(), runtime, ws)
    assert "insecure      = true" in (ws / "main.tf").read_text()


def test_state_password_escaped_in_hcl(tmp_path):
    runtime = TerraformRuntimeConfig(
        libvirt_uri="qemu:///system",
        state_url="https://state.devops.local",
        state_password='pa"ss\\word',
    )
    ws = tmp_path / "ws"
    render_terraform(_spec(), runtime, ws)
    main = (ws / "main.tf").read_text()
    assert 'pa\\"ss\\\\word' in main


def test_terraform_fmt_accepts_backend_block(tmp_path):
    if shutil.which("terraform") is None:
        pytest.skip("terraform CLI not installed")
    runtime = TerraformRuntimeConfig(
        libvirt_uri="qemu:///system",
        state_url="https://state.devops.local/terraform",
        state_username="cp-worker",
        state_password="s3cr3t",
    )
    ws = tmp_path / "ws"
    render_terraform(_spec(), runtime, ws)
    result = subprocess.run(
        ["terraform", "fmt", "-check", "-diff", "."], cwd=ws, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _ns_spec(**overrides) -> InfraSpec:
    data = {**FIXTURE, "mode": "namespace", **overrides}
    data["nodes"] = [{"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}]
    return InfraSpec.model_validate(data)


def test_namespace_light_tier_manifests():
    from controlplane.renderers.namespace import build_manifests

    manifests = build_manifests(_ns_spec())
    kinds = [m["kind"] for m in manifests]
    assert kinds == [
        "Namespace", "ResourceQuota", "LimitRange", "NetworkPolicy", "ServiceAccount", "ServiceMonitor",
    ]
    sm = next(m for m in manifests if m["kind"] == "ServiceMonitor")
    assert sm["metadata"]["namespace"] == "monitoring"
    assert sm["metadata"]["labels"]["platform.devops/project"] == "my-cluster"
    assert "platform.devops/tier" not in sm["metadata"]["labels"]


def test_namespace_full_tier_adds_elk_and_tier_label():
    from controlplane.renderers.namespace import build_manifests

    manifests = build_manifests(_ns_spec(observability="full"))
    kinds = [m["kind"] for m in manifests]
    assert "DaemonSet" in kinds and "ConfigMap" in kinds
    sm = next(m for m in manifests if m["kind"] == "ServiceMonitor")
    assert sm["metadata"]["labels"]["platform.devops/tier"] == "full"
    ds = next(m for m in manifests if m["kind"] == "DaemonSet")
    assert ds["metadata"]["labels"]["platform.devops/tier"] == "full"
