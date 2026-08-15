"""Coverage-gap tests: branches of core/, parsers/, renderers/ that the
existing suite does not reach (docs/TODO.md Task 1.5).

The security-critical modules must stay at >=85% line coverage, enforced in
CI by ``--cov-fail-under=85``. These tests exist because a hard gate without
the tests to back it is just a build that fails for no reason.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

_UUID_ONE = uuid.UUID(int=1)


# ---------------------------------------------------------------------------
# core/costs.py — billing branches (Task 5.1)
# ---------------------------------------------------------------------------


def _project(**overrides):
    from controlplane.models import Project

    defaults = {
        "id": uuid.uuid4(),
        "name": "costed",
        "owner_id": uuid.uuid4(),
        "infra_spec": {
            "nodes": [
                {"vcpu": 2, "memory_mb": 4096, "disk_gb": 30},
                {"vcpu": 4, "memory_mb": 8192, "disk_gb": 40},
            ]
        },
        "status": "ready",
        "created_at": datetime.now(UTC) - timedelta(hours=10),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Project(**defaults)


def test_cost_allocated_adds_across_nodes():
    from controlplane.core.costs import _allocated

    vcpu, memory_gb, disk_gb = _allocated(_project())
    assert (vcpu, disk_gb) == (6, 70)
    assert memory_gb == pytest.approx(12.0)


def test_cost_allocated_empty_spec_is_zero():
    from controlplane.core.costs import _allocated

    assert _allocated(_project(infra_spec={})) == (0, 0.0, 0)


def test_billable_hours_stops_at_destroy_time():
    from controlplane.core.costs import billable_hours

    destroyed = _project(
        status="destroyed",
        created_at=datetime.now(UTC) - timedelta(hours=24),
        updated_at=datetime.now(UTC) - timedelta(hours=3),
    )
    assert billable_hours(destroyed) == pytest.approx(21.0)


def test_billable_hours_naive_database_timestamps():
    from controlplane.core.costs import billable_hours

    # Postgres returns naive timestamps that are already UTC. A local
    # datetime.now() would be stamped as UTC and shift by the zone offset,
    # which is exactly why we replace tzinfo on a UTC value instead.
    naive = _project(
        created_at=(datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None),
    )
    assert billable_hours(naive) == pytest.approx(2.0, abs=0.1)


def test_billable_hours_never_negative():
    from controlplane.core.costs import billable_hours

    future = _project(created_at=datetime.now(UTC) + timedelta(hours=5))
    assert billable_hours(future) == 0.0


def test_estimate_uses_settings_prices_and_currency():
    from controlplane.core.costs import estimate

    breakdown = estimate(_project(), now=datetime.now(UTC))
    assert breakdown.currency == "EUR"
    assert breakdown.vcpu == 6
    assert breakdown.total > 0
    assert breakdown.as_dict()["total"] == round(breakdown.total, 2)


def test_summarise_totals_multiple_projects():
    from controlplane.core.costs import summarise

    summary = summarise([_project(name="a"), _project(name="b")])
    assert len(summary["projects"]) == 2
    assert summary["total"] == pytest.approx(
        sum(p["total"] for p in summary["projects"]), abs=0.01
    )


# ---------------------------------------------------------------------------
# core/config.py — env parsing failures must fail fast (never silent defaults)
# ---------------------------------------------------------------------------


def test_env_int_rejects_garbage(monkeypatch):
    from controlplane.core.config import _env_int

    monkeypatch.setenv("BOGUS_INT", "not-a-number")
    with pytest.raises(SystemExit):
        _env_int("BOGUS_INT", 5)


def test_env_float_rejects_garbage(monkeypatch):
    from controlplane.core.config import _env_float

    monkeypatch.setenv("BOGUS_FLOAT", "1.2.3")
    with pytest.raises(SystemExit):
        _env_float("BOGUS_FLOAT", 0.5)


def test_env_pool_parses_preset_counts(monkeypatch):
    from controlplane.core.config import _env_pool

    monkeypatch.setenv("WARM_POOL_TARGETS", "small=2, medium=1")
    assert _env_pool("WARM_POOL_TARGETS") == {"small": 2, "medium": 1}


def test_env_pool_empty_and_malformed(monkeypatch):
    from controlplane.core.config import _env_pool

    monkeypatch.setenv("WARM_POOL_TARGETS", "")
    assert _env_pool("WARM_POOL_TARGETS") == {}

    monkeypatch.setenv("WARM_POOL_TARGETS", "small=banana")
    with pytest.raises(SystemExit):
        _env_pool("WARM_POOL_TARGETS")


# ---------------------------------------------------------------------------
# core/vault.py — both KV versions, dev store, fail-closed paths (§7.4)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal stand-in for redis.Redis covering the DevSecretStore surface.

    Defined at module scope on purpose: test_no_test_function_returns_value
    walks each test function's AST and rejects any `return <value>`, including
    ones inside a nested class or def, so `get` cannot live inside the test.
    """

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)


def test_dev_secret_store_roundtrip():
    """DevSecretStore is Redis-backed, not an in-process dict.

    It used to keep a module-level ``_DEV_STORE`` dict, which this test cleared
    directly. That could never work outside a single process: the API mints the
    SSH keypair at registration and the worker reads it at provision time, and
    those are always separate processes, so provisioning never found the key.
    The store now writes to Redis; a fake client keeps the test hermetic and
    free of a live Redis (which is what the real dependency would otherwise
    require just to exercise get/set/delete).
    """
    from controlplane.core import vault as vault_mod

    store = vault_mod.DevSecretStore.__new__(vault_mod.DevSecretStore)
    store._client = _FakeRedis()

    store.set("u1", "ssh", "key-material")
    assert store.get("u1", "ssh") == "key-material"
    # Namespaced per user: another user must not see u1's secret.
    assert store.get("u2", "ssh") is None
    store.delete("u1", "ssh")
    assert store.get("u1", "ssh") is None


def test_secret_store_base_class_fails_closed():
    from controlplane.core.vault import SecretStore

    store = SecretStore()
    with pytest.raises(NotImplementedError):
        store.set("u", "k", "v")
    with pytest.raises(NotImplementedError):
        store.get("u", "k")
    with pytest.raises(NotImplementedError):
        store.delete("u", "k")


class _FakeKV2:
    def __init__(self, missing_read=False):
        self.written = {}
        self.missing_read = missing_read
        self.deleted = []

    def create_or_update_secret(self, path, secret):
        self.written[path] = secret

    def read_secret_version(self, path):
        if self.missing_read:
            raise Exception("secret not found")
        return {"data": {"data": {"value": f"v:{path}"}}}

    def delete_metadata_and_all_versions(self, path):
        self.deleted.append(path)


class _FakeKV1:
    def __init__(self):
        self.written = {}
        self.deleted = []

    def write(self, path, **kwargs):
        self.written[path] = kwargs

    def read(self, path):
        return {"data": {"value": f"v1:{path}"}}

    def delete(self, path):
        self.deleted.append(path)


class _FakeClient:
    def __init__(self, kv_version="2", missing_read=False):
        self.secrets = type("Secrets", (), {})()
        self.secrets.kv = type("KV", (), {})()
        if kv_version == "2":
            self.secrets.kv.v2 = _FakeKV2(missing_read=missing_read)
        else:
            self.kv1 = _FakeKV1()
            self.secrets.kv.v1 = self.kv1

    def write(self, path, **kwargs):
        self.kv1.write(path, **kwargs)

    def read(self, path):
        return self.kv1.read(path)

    def delete(self, path):
        self.kv1.delete(path)


def _patch_vault(monkeypatch, kv_version="2", missing_read=False):
    import controlplane.core.vault as vault
    from controlplane.core.config import settings

    object.__setattr__(settings, "vault_addr", "http://vault:8200")
    object.__setattr__(settings, "vault_token", "root")
    object.__setattr__(settings, "vault_kv_version", kv_version)
    client = _FakeClient(kv_version=kv_version, missing_read=missing_read)
    monkeypatch.setattr(
        vault, "hvac", type("HVAC", (), {"Client": lambda *a, **kw: client})()
    )
    return client


def test_vault_store_kv2_roundtrip(monkeypatch):
    from controlplane.core.vault import VaultSecretStore

    client = _patch_vault(monkeypatch, kv_version="2")
    store = VaultSecretStore()
    store.set("u1", "ssh", "key")
    assert client.secrets.kv.v2.written["controlplane/u1/ssh"] == {"value": "key"}
    assert store.get("u1", "ssh") == "v:controlplane/u1/ssh"
    store.delete("u1", "ssh")
    assert client.secrets.kv.v2.deleted == ["controlplane/u1/ssh"]


def test_vault_store_kv1_roundtrip(monkeypatch):
    from controlplane.core.vault import VaultSecretStore

    client = _patch_vault(monkeypatch, kv_version="1")
    store = VaultSecretStore()
    store.set("u1", "ssh", "key")
    assert client.secrets.kv.v1.written["controlplane/u1/ssh"] == {"value": "key"}
    assert store.get("u1", "ssh") == "v1:controlplane/u1/ssh"
    store.delete("u1", "ssh")
    assert client.secrets.kv.v1.deleted == ["controlplane/u1/ssh"]


def test_vault_store_get_missing_is_none_not_exception(monkeypatch):
    from controlplane.core.vault import VaultSecretStore

    _patch_vault(monkeypatch, kv_version="2", missing_read=True)
    assert VaultSecretStore().get("u1", "missing") is None


def test_get_secret_store_dev_backed(monkeypatch):
    import controlplane.core.vault as vault
    from controlplane.core.config import settings

    object.__setattr__(settings, "environment", "dev")
    object.__setattr__(settings, "vault_addr", "")
    vault._secret_store = None
    assert isinstance(vault.get_secret_store(), vault.DevSecretStore)
    # Second call returns the cached instance.
    assert vault.get_secret_store() is vault.get_secret_store()
    vault._secret_store = None


# ---------------------------------------------------------------------------
# core/pool.py + core/security.py (Task 2.5 core)
# ---------------------------------------------------------------------------


def test_spec_hash_is_stable_across_node_order_and_name():
    from controlplane.core.pool import spec_hash
    from controlplane.schemas.spec import InfraSpec

    spec = InfraSpec.model_validate(
        {
            "version": 1,
            "project": "pool-test",
            "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
            "nodes": [
                {"name": "master", "vcpu": 4, "memory_mb": 8192, "disk_gb": 50, "role": "k8s_master"},
                {"name": "worker-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_worker"},
            ],
        }
    )
    renamed = spec.model_copy(
        update={"project": "different-name", "nodes": list(reversed(spec.nodes))}
    )
    assert spec_hash(spec) == spec_hash(renamed)


def test_pool_target_lookup():
    from controlplane.core.pool import pool_target

    assert pool_target("abc", {"abc": 3}) == 3
    assert pool_target("unknown", {"abc": 3}) == 0


def test_security_helpers():
    from controlplane.core.security import (
        b64encode,
        decode_access_token,
        random_hex,
        random_secret,
    )

    assert decode_access_token("garbage") is None
    assert isinstance(random_secret(), str) and len(random_secret()) > 30
    assert len(random_hex()) == 64
    assert b64encode(b"hello") == "aGVsbG8"


# ---------------------------------------------------------------------------
# renderers/namespace.py — quota / netpol / SA shape (Task 2.3)
# ---------------------------------------------------------------------------


def _namespace_spec():
    from controlplane.schemas.spec import InfraSpec

    return InfraSpec.model_validate(
        {
            "version": 1,
            "project": "tenant-app",
            "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
            "nodes": [
                {"name": "node-1", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"},
            ],
        }
    )


def test_build_manifests_emits_isolation_documents():
    from controlplane.renderers.namespace import build_manifests

    docs = build_manifests(_namespace_spec())
    kinds = [d["kind"] for d in docs]
    # §4.1/4.2: every environment also gets a ServiceMonitor for the central
    # Prometheus, on top of the isolation documents.
    assert kinds == [
        "Namespace", "ResourceQuota", "LimitRange", "NetworkPolicy", "ServiceAccount", "ServiceMonitor",
    ]

    quota = docs[1]
    assert quota["spec"]["hard"]["limits.cpu"] == "2"
    assert quota["spec"]["hard"]["services.nodeports"] == "0"
    assert quota["spec"]["hard"]["services.loadbalancers"] == "0"

    netpol = docs[3]
    assert netpol["metadata"]["name"] == "tenant-app-default-deny"
    assert netpol["spec"]["policyTypes"] == ["Ingress", "Egress"]
    egress_cidrs = [
        rule["to"][0]["ipBlock"]["cidr"]
        for rule in netpol["spec"]["egress"]
        if "ipBlock" in rule["to"][0]
    ]
    assert "0.0.0.0/0" in egress_cidrs

    sa = docs[4]
    assert sa["automountServiceAccountToken"] is False
    assert sa["metadata"]["labels"]["platform.devops/mode"] == "namespace"


def test_render_namespace_writes_single_yaml_file(tmp_path):
    from controlplane.renderers.namespace import render_namespace

    path = render_namespace(_namespace_spec(), tmp_path / "ns")
    assert path.name == "namespace.yaml"
    assert "ResourceQuota" in path.read_text()


# ---------------------------------------------------------------------------
# renderers internals — HCL scalar edge cases (Task 1.3 honesty)
# ---------------------------------------------------------------------------


def test_hcl_scalar_or_block_variants():
    from controlplane.renderers.terraform import _hcl_attr_lines, _hcl_scalar_or_block

    assert _hcl_scalar_or_block(True) == "true"
    assert _hcl_scalar_or_block(42) == "42"
    assert _hcl_scalar_or_block(["a", "b"]) == '["a", "b"]'
    assert _hcl_scalar_or_block([True, 3]) == "[true, 3]"
    assert _hcl_scalar_or_block("line1\nline2") == "line1\nline2"
    block = _hcl_scalar_or_block({"a": 1, "b": "x"})
    assert block.startswith("{\n") and "a" in block
    assert _hcl_attr_lines({}) == ""


def test_ip_of_unknown_node_raises():
    from controlplane.renderers.ansible import _ip_of

    with pytest.raises(KeyError):
        _ip_of(_namespace_spec(), "ghost-node")


# ---------------------------------------------------------------------------
# parsers — CVSS-vector severities and malformed inputs (Task 1.5)
# ---------------------------------------------------------------------------


def test_pip_audit_cvss_vector_severity_mapping():
    from controlplane.parsers.pip_audit_parser import parse_pip_audit

    payload = {
        "dependencies": [
            {
                "name": "cryptography",
                "version": "1.2.3",
                "vulns": [
                    {
                        "id": "GHSA-1",
                        "severity": {
                            "cvssV3": {
                                "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"
                            }
                        },
                        "fix_versions": ["2.0"],
                    }
                ],
            }
        ]
    }
    parsed = parse_pip_audit(__import__("json").dumps(payload))
    assert parsed.summary["critical"] == 1
    assert parsed.findings[0]["fixed_version"] == "2.0"


def test_pip_audit_cvss_without_vector_uses_score():
    from controlplane.parsers.pip_audit_parser import parse_pip_audit

    payload = {
        "dependencies": [
            {
                "name": "requests",
                "version": "1.0",
                "vulns": [
                    {"id": "CVE-1", "severity": {"score": 8.5}, "fix_versions": []}
                ],
            }
        ]
    }
    parsed = parse_pip_audit(__import__("json").dumps(payload))
    assert parsed.summary["high"] == 1
    assert parsed.findings[0]["fixed_version"] is None

# ---------------------------------------------------------------------------
# Test hygiene (docs/TODO.md §8 item 10) — pytest will error on test
# functions returning non-None in a future release; this guard catches the
# pattern (including values returned from branches) early.
# ---------------------------------------------------------------------------


def test_no_test_function_returns_value():
    import ast
    import glob

    def _returns_within(body):
        found = []
        for stmt in body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue
            for node in ast.walk(stmt):
                if isinstance(node, ast.Return) and node.value is not None:
                    found.append(node.lineno)
        return found

    offenders = []
    here = glob.glob(f"{__import__('pathlib').Path(__file__).parent}/*.py")
    for path in here:
        tree = ast.parse(open(path).read())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                for lineno in _returns_within(node.body):
                    offenders.append(f"{path}:{lineno} ({node.name})")
    assert offenders == [], f"test functions must not return values: {offenders}"
