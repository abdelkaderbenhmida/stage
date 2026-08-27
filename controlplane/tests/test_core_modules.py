"""Coverage-gap tests, part 2: presets, redaction, repo URLs, roles, runtime
wiring, SSH keys and the remaining security/pool/vault branches.

The claim_cluster tests need a real database and are marked ``integration``;
everything else here is unit-level and runs everywhere.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# core/presets.py (Task 2.1)
# ---------------------------------------------------------------------------


def test_expand_preset_small():
    from controlplane.core.presets import expand_preset

    spec = expand_preset("small", "project-x")
    assert spec["project"] == "project-x"
    assert spec["nodes"] == [
        {"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}
    ]


def test_expand_preset_medium_adds_workers():
    from controlplane.core.presets import expand_preset

    spec = expand_preset("medium", "p", {"cidr": "10.0.0.0/24", "domain": "x.local"})
    roles = [n["role"] for n in spec["nodes"]]
    assert roles == ["k8s_master", "k8s_worker"]
    assert spec["network"] == {"cidr": "10.0.0.0/24", "domain": "x.local"}


def test_expand_preset_unknown_raises():
    from controlplane.core.presets import expand_preset

    with pytest.raises(ValueError, match="Unknown preset"):
        expand_preset("huge", "p")


def test_preset_size_monotonicity():
    from controlplane.core.presets import PRESETS

    vcpus = [PRESETS[name].vcpu * PRESETS[name].node_count for name in ("small", "medium", "large")]
    assert vcpus == sorted(vcpus)
    assert PRESETS["small"].nodes()[0]["name"] == "master"


# ---------------------------------------------------------------------------
# core/redaction.py (PLATFORM_SPEC §7.4)
# ---------------------------------------------------------------------------


def test_scrub_line_catches_credential_patterns():
    from controlplane.core.redaction import scrub_line

    fixtures = [
        "deploy with AKIA1234567890ABCDEFGH",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nabc",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig-more",
        "password=sup3r_secret_value",
        "token: xyz",
        "sshpass -p hunter2 rsync ...",
        "clone https://x/ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij/repo.git",
    ]
    for line in fixtures:
        assert scrub_line(line) == "[REDACTED]", line


def test_scrub_line_allowlisted_context_survives():
    from controlplane.core.redaction import scrub_line

    assert scrub_line("password_hash = sha256:deadbeef") == "password_hash = sha256:deadbeef"
    assert scrub_line("see example.com docs") == "see example.com docs"


def test_scrub_line_clean_text_passes_through():
    from controlplane.core.redaction import scrub_line

    assert scrub_line("kubectl get pods -n default") == "kubectl get pods -n default"


def test_scrub_stream_strips_newlines_and_yields_idempotent():
    from controlplane.core.redaction import scrub_stream

    out = list(scrub_stream(["line1\n", "token=abc\n", "line3"]))
    assert out == ["line1", "[REDACTED]", "line3"]


# ---------------------------------------------------------------------------
# core/repo_url.py (§7.5 SSRF guard)
# ---------------------------------------------------------------------------


def test_validate_repo_url_good():
    from controlplane.core.repo_url import validate_repo_url

    assert (
        validate_repo_url("  https://GitHub.com/acme/repo.git  ")
        == "https://GitHub.com/acme/repo.git"
    )


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:acme/repo.git",
        "ssh://git@github.com/acme/repo.git",
        "file:///etc/passwd",
        "http://github.com/acme/repo.git",
        "https://evil.com/repo.git",
        "https://github.com.evil.com/repo.git",
    ],
)
def test_validate_repo_url_rejects(url):
    from controlplane.core.repo_url import InvalidRepoUrl, validate_repo_url

    with pytest.raises(InvalidRepoUrl):
        validate_repo_url(url)


# ---------------------------------------------------------------------------
# core/roles.py — the §3.2 table is the single source of truth; here we only
# lock its shape so someone collapsing a role breaks loudly.
# ---------------------------------------------------------------------------


def test_action_roles_table_shape():
    from controlplane.core.roles import ACTION_ROLES

    assert ACTION_ROLES["project.read"] == "viewer"
    assert ACTION_ROLES["project.destroy"] == "owner"
    assert ACTION_ROLES["team.manage"] == "admin"
    assert set(ACTION_ROLES.values()) == {"viewer", "developer", "owner", "admin"}


# ---------------------------------------------------------------------------
# core/runtime.py — wiring from settings + vault into renderer configs
# ---------------------------------------------------------------------------


def test_project_workspace_under_root(monkeypatch):
    import controlplane.core.runtime as runtime
    from controlplane.core.config import settings

    object.__setattr__(settings, "workspace_root", "/tmp/cp-ws")
    project_id = uuid.uuid4()
    assert runtime.project_workspace(project_id) == __import__(
        "pathlib"
    ).Path("/tmp/cp-ws") / str(project_id)


def test_terraform_runtime_injects_ssh_key_from_vault(monkeypatch):
    import controlplane.core.runtime as runtime
    from controlplane.core.config import settings

    object.__setattr__(settings, "environment", "dev")
    object.__setattr__(settings, "vault_addr", "")
    from test_coverage_gaps import _UUID_ONE

    runtime.get_secret_store().set(str(_UUID_ONE), "ssh_public_key", "ssh-ed25519 AAAAtest")
    config = runtime.terraform_runtime(uuid.UUID(int=1))
    assert config.ssh_user == "devops"
    assert config.ssh_public_key == "ssh-ed25519 AAAAtest"
    assert config.libvirt_uri == settings.libvirt_uri


def test_terraform_runtime_empty_key_when_unset(monkeypatch):
    """No stored key for this user -> empty ssh_public_key, not a crash.

    Previously cleared the module-level ``_DEV_STORE`` dict. DevSecretStore is
    now Redis-backed (the API and worker are separate processes, so an
    in-process dict was invisible across them), so the isolation this test
    needs comes from using a user id whose key is explicitly deleted rather
    than from wiping a shared dict.
    """
    import controlplane.core.runtime as runtime

    object.__setattr__(runtime.settings, "environment", "dev")
    object.__setattr__(runtime.settings, "vault_addr", "")
    user_id = uuid.UUID(int=2)
    runtime.get_secret_store().delete(str(user_id), "ssh_public_key")
    config = runtime.terraform_runtime(user_id)
    assert config.ssh_public_key == ""


def test_ansible_runtime_and_private_key(monkeypatch):
    import controlplane.core.runtime as runtime

    object.__setattr__(runtime.settings, "environment", "dev")
    object.__setattr__(runtime.settings, "vault_addr", "")
    assert runtime.ansible_runtime().ssh_user == "devops"
    runtime.get_secret_store().set(str(uuid.UUID(int=1)), "ssh_private_key", "PRIVATE")
    assert runtime.user_ssh_private_key(uuid.UUID(int=1)) == "PRIVATE"


# ---------------------------------------------------------------------------
# core/sshkeys.py
# ---------------------------------------------------------------------------


def test_generate_ssh_keypair_returns_usable_pem():
    from controlplane.core.sshkeys import generate_ssh_keypair
    from cryptography.hazmat.primitives import serialization

    private_pem, public_pem = generate_ssh_keypair()
    assert private_pem.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert public_pem.startswith("ssh-ed25519 ")
    loaded = serialization.load_ssh_private_key(
        private_pem.encode(), password=None
    )
    assert loaded.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode() == public_pem


# ---------------------------------------------------------------------------
# core/security.py — Argon2, JWT primitives
# ---------------------------------------------------------------------------


def test_password_hash_verify_roundtrip():
    from controlplane.core.security import hash_password, verify_password

    digest = hash_password("S3cret!")
    assert digest != "S3cret!"
    assert verify_password("S3cret!", digest)
    assert not verify_password("wrong", digest)


def test_password_verify_never_raises_on_corrupt_hash():
    from controlplane.core.security import verify_password

    assert not verify_password("anything", "not-a-hash")


def test_access_token_roundtrip_with_role():
    from controlplane.core.security import create_access_token, decode_access_token

    token = create_access_token("user-1", role="owner")
    payload = decode_access_token(token)
    assert payload["sub"] == "user-1"
    assert payload["role"] == "owner"
    assert payload["type"] == "access"


def test_decode_rejects_non_access_and_foreign_tokens(monkeypatch):
    import jwt as pyjwt
    from controlplane.core import security
    from controlplane.core.config import settings

    other = pyjwt.encode({"sub": "x", "type": "refresh"}, "other-secret", algorithm="HS256")
    assert security.decode_access_token(other) is None

    refresh = pyjwt.encode(
        {"sub": "x", "type": "refresh"},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    assert security.decode_access_token(refresh) is None

    expired = security._create_token("x", "access", -5)
    assert security.decode_access_token(expired) is None


def test_refresh_token_digest_and_helpers():
    from controlplane.core.security import (
        b64encode,
        generate_refresh_token,
        hash_refresh_token,
        random_hex,
        random_secret,
    )

    token = generate_refresh_token()
    assert token != hash_refresh_token(token)
    assert len(hash_refresh_token(token)) == 64
    assert len(random_hex(16)) == 32
    assert len(random_secret()) == 86
    assert b64encode(b"a\xff") == "Yf8"


# ---------------------------------------------------------------------------
# core/pool.py — warm-pool claim (integration: needs the real database)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_claim_cluster_claims_oldest_available(session):
    from controlplane.core.pool import claim_cluster, spec_hash
    from controlplane.models.pool import PooledCluster
    from controlplane.models.project import Project
    from controlplane.models.user import User
    from controlplane.repositories.teams import ensure_personal_team
    from controlplane.schemas.spec import InfraSpec

    user = User(email="pool-owner@example.com", password_hash="x")
    session.add(user)
    session.flush()
    team = ensure_personal_team(session, user)
    project = Project(owner_id=user.id, team_id=team.id, name="claim-me", status="ready", infra_spec={})
    session.add(project)
    session.commit()

    spec = InfraSpec.model_validate(
        {
            "version": 1,
            "project": "claim-me",
            "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
            "nodes": [
                {"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}
            ],
        }
    )
    fingerprint = spec_hash(spec)
    for i, created in enumerate([1, 0]):
        session.add(
            PooledCluster(
                spec_hash=fingerprint,
                status="available",
                workspace_path=f"/ws/{i}",
                created_at=datetime.now(UTC) - timedelta(days=created),
            )
        )
    session.commit()

    claimed = claim_cluster(session, spec, project.id)
    assert claimed is not None
    assert claimed.workspace_path == "/ws/0"  # oldest first (created 1 day ago)
    assert claimed.status == "claimed"
    assert claimed.claimed_by_project_id == project.id
    session.commit()

    second = claim_cluster(session, spec, project.id)
    assert second is not None
    assert second.workspace_path == "/ws/1"
    assert second.status == "claimed"
    session.commit()

    # Pool exhausted: nothing available remains.
    assert claim_cluster(session, spec, project.id) is None


@pytest.mark.integration
def test_claim_cluster_returns_none_when_pool_mismatched(session):
    from controlplane.core.pool import claim_cluster
    from controlplane.schemas.spec import InfraSpec

    spec = InfraSpec.model_validate(
        {
            "version": 1,
            "project": "claim-none",
            "network": {"cidr": "192.168.56.0/24", "domain": "devops.local"},
            "nodes": [
                {"name": "master", "vcpu": 2, "memory_mb": 4096, "disk_gb": 30, "role": "k8s_master"}
            ],
        }
    )
    assert claim_cluster(session, spec, uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# core/vault.py — kv1 read/decode path and delete-failure containment
# ---------------------------------------------------------------------------


def test_vault_kv1_read_and_delete(monkeypatch):
    from controlplane.core.vault import VaultSecretStore
    from test_coverage_gaps import _patch_vault

    client = _patch_vault(monkeypatch, kv_version="1")
    store = VaultSecretStore()
    assert store.get("u1", "ssh") == "v1:controlplane/u1/ssh"
    store.delete("u1", "ssh")
    assert client.kv1.deleted == ["controlplane/u1/ssh"]


def test_vault_delete_failure_is_contained(monkeypatch):
    from controlplane.core.vault import VaultSecretStore
    from test_coverage_gaps import _patch_vault

    client = _patch_vault(monkeypatch, kv_version="2")

    def _boom(path):
        raise RuntimeError("vault down")

    client.secrets.kv.v2.delete_metadata_and_all_versions = _boom
    VaultSecretStore().delete("u1", "ssh")  # must not raise


def test_vault_get_secret_store_vault_backed(monkeypatch):
    import controlplane.core.vault as vault

    object.__setattr__(vault.settings, "environment", "prod")
    vault._secret_store = None

    class _BareClient:
        pass

    monkeypatch.setattr(vault, "hvac", type("HVAC", (), {"Client": lambda *a, **kw: _BareClient()})())
    store = vault.get_secret_store()
    assert isinstance(store, vault.VaultSecretStore)
    vault._secret_store = None
    object.__setattr__(vault.settings, "environment", "dev")


# ---------------------------------------------------------------------------
# parsers — leftover branches
# ---------------------------------------------------------------------------


def test_gitleaks_parser_dict_shape():
    from controlplane.parsers.gitleaks_parser import parse_gitleaks

    parsed = parse_gitleaks(
        __import__("json").dumps(
            {"leaks": [{"RuleID": "aws-key", "Secret": "AKIA1234567890ABCDEFGH", "File": "x.sh"}]}
        )
    )
    assert parsed.findings[0]["identifier"] == "aws-key"
    assert parsed.summary["high"] == 1


def test_pip_audit_score_boundaries():
    from controlplane.parsers.pip_audit_parser import parse_pip_audit

    def _one(score):
        return parse_pip_audit(
            __import__("json").dumps(
                {
                    "dependencies": [
                        {
                            "name": "pkg",
                            "version": "1.0",
                            "vulns": [
                                {"id": "CVE-x", "severity": {"score": score}, "fix_versions": []}
                            ],
                        }
                    ]
                }
            )
        ).summary

    assert _one(0.5)["low"] == 1
    assert _one(4.0)["medium"] == 1
    assert _one(1.0) == _one(1.0)  # smoke: result stable


def test_pip_audit_no_severity_at_all():
    from controlplane.parsers.pip_audit_parser import parse_pip_audit

    parsed = parse_pip_audit(
        __import__("json").dumps(
            {
                "dependencies": [
                    {
                        "name": "pkg",
                        "version": "1.0",
                        "vulns": [{"id": "CVE-y", "severity": None, "fix_versions": []}],
                    }
                ]
            }
        )
    )
    assert parsed.summary["unknown"] == 1

def test_a_loopback_kubeconfig_is_refused_with_the_fix_in_the_message(tmp_path):
    """127.0.0.1 in a kubeconfig is the sandbox container, not the cluster.

    kubectl runs inside a container on the sandbox network, so a kubeconfig
    written for the host — kind's default — fails with `dial tcp
    127.0.0.1:PORT: connect: connection refused`, which names nothing an
    operator can act on. The address is knowable before the run.
    """
    import pytest
    from controlplane.workers import tasks

    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("clusters:\n- cluster:\n    server: https://127.0.0.1:42827\n")
    with pytest.raises(RuntimeError) as excinfo:
        tasks._reject_loopback_kubeconfig(kubeconfig)
    assert "kind get kubeconfig --internal" in str(excinfo.value)


def test_a_reachable_kubeconfig_is_left_alone(tmp_path):
    from controlplane.workers import tasks

    kubeconfig = tmp_path / "config"
    kubeconfig.write_text("clusters:\n- cluster:\n    server: https://cluster.internal:6443\n")
    tasks._reject_loopback_kubeconfig(kubeconfig)


def test_an_unwritable_workspace_root_names_the_setting_that_moves_it(tmp_path, settings_override):
    """The tenant reads this message; "[Errno 13] /var/lib/controlplane" is
    not something they can act on, and the condition belongs to the install."""
    import pytest
    from controlplane.workers import tasks

    blocked = tmp_path / "no-write"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        with settings_override(workspace_root=str(blocked / "workspaces")):
            with pytest.raises(RuntimeError) as excinfo:
                tasks._ensure_workspace_root()
        assert "WORKSPACE_ROOT" in str(excinfo.value)
    finally:
        blocked.chmod(0o700)
