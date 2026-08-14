"""Phase 2 acceptance: every rule in docs/PLATFORM_SPEC.md §6.2."""

import pytest
from controlplane.schemas.spec import InfraSpec
from pydantic import ValidationError


def _base(overrides: dict | None = None) -> dict:
    spec = {
        "version": 1,
        "project": "my-cluster",
        "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
        "nodes": [
            {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
            {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
        ],
    }
    if overrides:
        spec.update(overrides)
    return spec


def _expect_fail(spec: dict):
    with pytest.raises(ValidationError):
        InfraSpec.model_validate(spec)


def _expect_ok(spec: dict):
    InfraSpec.model_validate(spec)


@pytest.mark.parametrize("nodes", [
    [{"name": "n1", "vcpu": 1, "memory_mb": 1024, "disk_gb": 20, "role": "docker_host"}],
    [{"name": "n1", "vcpu": 8, "memory_mb": 16384, "disk_gb": 200, "role": "docker_host"}],
    [{"name": "n1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 50, "role": "k8s_master"},
     {"name": "n2", "vcpu": 2, "memory_mb": 4096, "disk_gb": 50, "role": "k8s_worker"},
     {"name": "n3", "vcpu": 2, "memory_mb": 4096, "disk_gb": 50, "role": "k8s_worker"}],
])
def test_valid_specs(nodes):
    _expect_ok(_base({"nodes": nodes}))


@pytest.mark.parametrize("spec,expected_note", [
    (_base({"nodes": [n for n in [
        {"name": f"n{i}", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "docker_host"}
        for i in range(11)
    ]]}), "1 and 10"),  # 11 nodes
    (_base({"nodes": [{"name": "n1", "vcpu": 9, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}]}), "vcpu"),
    (_base({"nodes": [{"name": "n1", "vcpu": 0, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}]}), "vcpu"),
    (_base({"nodes": [{"name": "n1", "vcpu": 2, "memory_mb": 999, "disk_gb": 30, "role": "k8s_master"}]}), "memory"),
    (_base({"nodes": [{"name": "n1", "vcpu": 2, "memory_mb": 20000, "disk_gb": 30, "role": "k8s_master"}]}), "memory"),
    (_base({"nodes": [{"name": "n1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 10, "role": "k8s_master"}]}), "disk"),
    (_base({"nodes": [{"name": "n1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 300, "role": "k8s_master"}]}), "disk"),
])
def test_per_node_limits(spec, expected_note):
    _expect_fail(spec)


def test_two_masters_rejected():
    _expect_fail(_base({"nodes": [
        {"name": "m1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
        {"name": "m2", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
        {"name": "w1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
    ]}))


def test_zero_masters_with_workers_rejected():
    _expect_fail(_base({"nodes": [
        {"name": "w1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
        {"name": "w2", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
    ]}))


def test_all_docker_host_no_master_ok():
    _expect_ok(_base({"nodes": [
        {"name": "d1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "docker_host"},
        {"name": "d2", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "docker_host"},
    ]}))


def test_duplicate_node_names_rejected():
    _expect_fail(_base({"nodes": [
        {"name": "dup", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
        {"name": "dup", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
    ]}))


@pytest.mark.parametrize("name", ["../../etc/passwd", "master; rm -rf /", "UPPER", "under_score", "a" * 21])
def test_hostile_node_names_rejected(name):
    _expect_fail(_base({"nodes": [
        {"name": name, "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}
    ]}))


@pytest.mark.parametrize("role", ["root", "admin", "k8s-master", ""])
def test_invalid_roles_rejected(role):
    _expect_fail(_base({"nodes": [
        {"name": "n1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": role}
    ]}))


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "8.8.8.0/24", "192.168.1.0/29", "172.16.0.0/15", "10.0.0.0/8", "not-a-cidr"])
def test_invalid_cidrs_rejected(cidr):
    _expect_fail(_base({"network": {"cidr": cidr, "domain": "devops.local"}}))


@pytest.mark.parametrize("cidr", ["10.0.0.0/16", "172.16.0.0/28", "192.168.100.0/24", "172.31.255.0/24"])
def test_valid_cidrs_ok(cidr):
    _expect_ok(_base({"network": {"cidr": cidr, "domain": "devops.local"}}))


def test_total_vcpu_cap():
    # Each node is individually legal (5 <= 8 vCPU); only the sum breaks the
    # 24 vCPU project cap. 5 nodes x 5 vCPU = 25.
    nodes = [{"name": f"n{i}", "vcpu": 5, "memory_mb": 8192, "disk_gb": 40, "role": "docker_host"} for i in range(5)]
    _expect_fail(_base({"nodes": nodes}))


def test_total_memory_cap():
    # 4 nodes x 16384 = 65536 > 49152, each individually legal
    nodes = [{"name": f"n{i}", "vcpu": 2, "memory_mb": 16384, "disk_gb": 40, "role": "docker_host"} for i in range(4)]
    _expect_fail(_base({"nodes": nodes}))


@pytest.mark.parametrize("version", ["1.26", "1.30", "v1.28", "latest"])
def test_invalid_k8s_version_rejected(version):
    _expect_fail(_base({"config": {"kubernetes_version": version}}))


@pytest.mark.parametrize("runtime", ["docker", "cri-o", "containerdd"])
def test_invalid_container_runtime_rejected(runtime):
    _expect_fail(_base({"config": {"container_runtime": runtime}}))


@pytest.mark.parametrize("cni", ["weave", "cilium-1", ""])
def test_invalid_cni_rejected(cni):
    _expect_fail(_base({"config": {"cni_plugin": cni}}))


@pytest.mark.parametrize("version", ["23.0", "26.0", "latest"])
def test_invalid_docker_version_rejected(version):
    _expect_fail(_base({"config": {"docker_version": version}}))


def test_unknown_top_level_key_rejected():
    _expect_fail(_base({"extra_field": "boom"}))


def test_unknown_node_key_rejected():
    _expect_fail(_base({"nodes": [
        {"name": "n1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master", "provisioner": "local-exec"}
    ]}))


def test_unknown_config_key_rejected():
    _expect_fail(_base({"config": {"kubernetes_version": "1.28", "malicious_var": "x"}}))


def test_unknown_network_key_rejected():
    _expect_fail(_base({"network": {"cidr": "192.168.1.0/24", "domain": "d.local", "gateway": "evil"}}))


def test_bad_project_name_rejected():
    _expect_fail(_base({"project": "../x"}))
    _expect_fail(_base({"project": "AB"}))
    _expect_fail(_base({"project": "ab"}))


def test_unsupported_version_rejected():
    _expect_fail(_base({"version": 2}))


def test_error_messages_are_human_readable():
    with pytest.raises(ValidationError) as exc:
        InfraSpec.model_validate(_base({"nodes": [
            {"name": "n1", "vcpu": 99, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}
        ]}))
    joined = " ".join(e["msg"] for e in exc.value.errors())
    assert "99" in joined
    assert "vcpu" in joined
