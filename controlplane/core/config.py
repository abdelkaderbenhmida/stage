"""Typed settings for the control plane.

Reads everything from the environment, following the pattern in
``app/shared/config.py``. Missing values fail fast on first use rather than
silently falling back to insecure defaults.
"""

import json
import os
from dataclasses import dataclass, field


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_json(key: str, default: dict) -> dict:
    """Parse a JSON object from an environment variable (e.g. the OIDC
    group -> role map). A malformed value fails fast; an empty value falls
    back to the default."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON for {key}: {raw!r}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Expected a JSON object for {key}, got {type(value).__name__}.")
    return value


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer for {key}: {raw!r}") from exc


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid number for {key}: {raw!r}") from exc


def _env_pool(key: str) -> dict[str, int]:
    """Parse "small=2,medium=1" into {"small": 2, "medium": 1}.

    An unparseable value fails fast rather than silently disabling the pool,
    which would otherwise look like the feature simply not working.
    """
    raw = os.environ.get(key, "")
    if not raw.strip():
        return {}
    targets: dict[str, int] = {}
    for chunk in raw.split(","):
        name, _, count = chunk.partition("=")
        name = name.strip()
        if not name or not count.strip().isdigit():
            raise SystemExit(f"Invalid entry {chunk!r} in {key}: expected 'preset=count'.")
        targets[name] = int(count)
    return targets


@dataclass(frozen=True)
class Settings:
    # Deployment context
    environment: str = field(default_factory=lambda: _env("ENVIRONMENT", "production"))
    debug: bool = field(default_factory=lambda: _env("DEBUG", "false").lower() == "true")
    # "json" emits structured lines with request ids; "plain" is human-readable
    log_format: str = field(default_factory=lambda: _env("LOG_FORMAT", "plain"))

    # PostgreSQL
    database_url: str = field(default_factory=lambda: _env(
        "DATABASE_URL",
        "postgresql+psycopg://platform:platform@localhost:5432/platform",
    ))
    # Connection pool limits (§7): the API and each Celery worker run
    # several times over per process, so leaving SQLAlchemy defaults
    # multiplies connections by process count and exhausts PostgreSQL.
    db_pool_size: int = field(default_factory=lambda: _env_int("PG_POOL_SIZE", 5))
    db_max_overflow: int = field(default_factory=lambda: _env_int("PG_MAX_OVERFLOW", 10))
    db_pool_recycle: int = field(default_factory=lambda: _env_int("PG_POOL_RECYCLE", 1800))

    # Redis / Celery
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))

    # §4.2: the UI logs view proxies the central Loki (k8s/monitoring/loki).
    loki_url: str = field(
        default_factory=lambda: _env("LOKI_URL", "http://loki.monitoring.svc.cluster.local:3100")
    )
    # The project monitoring panel proxies the central Prometheus the same way
    # the logs view proxies Loki: server-side PromQL, forced to the caller's
    # own namespace. Tenants never reach Prometheus (or Grafana) directly —
    # it is one shared TSDB with no per-tenant access control of its own.
    prometheus_url: str = field(
        default_factory=lambda: _env("PROMETHEUS_URL", "http://prometheus.monitoring.svc.cluster.local:9090")
    )

    # Security
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", ""))
    jwt_algorithm: str = field(default_factory=lambda: _env("JWT_ALGORITHM", "HS256"))
    access_token_minutes: int = field(default_factory=lambda: _env_int("ACCESS_TOKEN_MINUTES", 30))
    refresh_token_days: int = field(default_factory=lambda: _env_int("REFRESH_TOKEN_DAYS", 30))

    # Vault (blank in dev => ephemeral in-memory secret store)
    vault_addr: str = field(default_factory=lambda: _env("VAULT_ADDR", ""))
    vault_token: str = field(default_factory=lambda: _env("VAULT_TOKEN", ""))
    vault_mount: str = field(default_factory=lambda: _env("VAULT_MOUNT", "secret"))
    vault_kv_version: str = field(default_factory=lambda: _env("VAULT_KV_VERSION", "2"))
    # Where operator/configuration secrets live (§7 item 2): jwt_secret,
    # registry_password, tf_state_password, oidc_client_secret.
    vault_secrets_path: str = field(default_factory=lambda: _env("VAULT_SECRETS_PATH", "controlplane/config"))

    # Worker sandbox
    sandbox_image: str = field(default_factory=lambda: _env(
        "SANDBOX_IMAGE", "platform/sandbox:latest"))
    sandbox_network_enabled: bool = field(
        default_factory=lambda: _env("SANDBOX_NETWORK_ENABLED", "true").lower() == "true")
    provision_timeout_seconds: int = field(
        default_factory=lambda: _env_int("PROVISION_TIMEOUT_SECONDS", 900))
    scan_timeout_seconds: int = field(
        default_factory=lambda: _env_int("SCAN_TIMEOUT_SECONDS", 300))
    sandbox_cpus: float = field(default_factory=lambda: float(_env("SANDBOX_CPUS", "2")))
    sandbox_memory_mb: int = field(default_factory=lambda: _env_int("SANDBOX_MEMORY_MB", 1024))
    # Trivy's vulnerability DB is roughly a gigabyte. Every scan runs in a
    # fresh --rm sandbox container, so without a persistent cache it
    # re-downloads the whole DB on every single scan — slow, wasteful, and
    # the first cause of scan-gate timeouts on anything but a fast link.
    # A Docker-managed named volume (not a host bind-mount) so ownership and
    # permissions are Docker's problem, not this process's — the volume is
    # created automatically on first use.
    trivy_cache_volume: str = field(default_factory=lambda: _env(
        "TRIVY_CACHE_VOLUME", "controlplane-trivy-cache"))

    # DNS suffix the tenant live URL and ingress host are built from. Not a
    # constant: it is whatever the cluster's ingress actually answers on, so
    # it has to move with the environment rather than be baked into the code.
    cluster_domain: str = field(default_factory=lambda: _env("CLUSTER_DOMAIN", "devops.local"))
    # Where the filebeat sidecar rendered into a tenant namespace ships logs.
    # Same reasoning as loki_url/prometheus_url: an in-cluster address that is
    # only right for one deployment of this platform.
    elasticsearch_url: str = field(default_factory=lambda: _env(
        "ELASTICSEARCH_URL", "http://elasticsearch.monitoring.svc.cluster.local:9200"))

    # Auto-generated Dockerfile (repositories that ship none of their own).
    # The base image and the pinned upgrades belong in config because they
    # are exactly what has to change when a new CVE lands: the pre-deploy
    # Trivy gate blocks on HIGH, so a stale base image here silently blocks
    # every auto-built deployment until someone can bump it without a
    # release.
    autobuild_base_image: str = field(default_factory=lambda: _env(
        "AUTOBUILD_BASE_IMAGE", "python:3.11-slim"))
    # Applied on top of the base image to clear the fixable HIGHs it ships
    # (wheel and jaraco.context are vendored inside setuptools, so the
    # standalone upgrade alone does not settle them).
    autobuild_pip_hardening: str = field(default_factory=lambda: _env(
        "AUTOBUILD_PIP_HARDENING", "wheel>=0.46.2 jaraco.context>=6.1.0 setuptools>=84"))
    autobuild_server_package: str = field(default_factory=lambda: _env(
        "AUTOBUILD_SERVER_PACKAGE", "uvicorn[standard]>=0.24.0"))
    # Non-root uid the generated image runs as. Configurable because a
    # cluster with a restrictive PodSecurity range will reject a uid outside
    # it, and that is an environment fact, not a code one.
    autobuild_run_uid: int = field(default_factory=lambda: _env_int("AUTOBUILD_RUN_UID", 10001))

    # Libvirt host defaults (injected into rendered Terraform, never user-set)
    libvirt_uri: str = field(default_factory=lambda: _env("LIBVIRT_URI", "qemu:///system"))
    storage_pool: str = field(default_factory=lambda: _env("STORAGE_POOL", "default"))
    base_image_path: str = field(default_factory=lambda: _env(
        "BASE_IMAGE_PATH", "/var/lib/libvirt/images/ubuntu-22.04-server-cloudimg-amd64.img"))
    network_interface: str = field(default_factory=lambda: _env("NETWORK_INTERFACE", "enp1s0"))
    dns_servers: list = field(
        default_factory=lambda: [
            item.strip() for item in _env("DNS_SERVERS", "1.1.1.1,8.8.8.8").split(",") if item.strip()
        ]
    )
    libvirt_volume_owner_uid: int = field(default_factory=lambda: _env_int("LIBVIRT_VOLUME_OWNER_UID", 64055))
    libvirt_volume_group_gid: int = field(default_factory=lambda: _env_int("LIBVIRT_VOLUME_GROUP_GID", 993))
    workspace_root: str = field(default_factory=lambda: _env("WORKSPACE_ROOT", "/var/lib/controlplane/workspaces"))

    # Terraform remote state (§7 item 1): per-project HTTP backend. When
    # unset, rendered workspaces use local state (dev).
    tf_state_url: str = field(default_factory=lambda: _env("TF_STATE_URL"))
    tf_state_username: str = field(default_factory=lambda: _env("TF_STATE_USERNAME"))
    tf_state_password: str = field(default_factory=lambda: _env("TF_STATE_PASSWORD"))
    tf_state_insecure: bool = field(
        default_factory=lambda: _env("TF_STATE_INSECURE", "false").lower() == "true")

    # Registry used by the deployment pipeline
    registry: str = field(default_factory=lambda: _env("REGISTRY", "localhost:5000"))
    # How the registry is addressed from *inside* a sandbox container, and the
    # docker network that name resolves on. The control plane pushes to
    # `registry` (published on the host); a sandbox cannot reach that address,
    # so the scanner needs the registry's name on its own network.
    registry_internal: str = field(default_factory=lambda: _env("REGISTRY_INTERNAL", "kind-registry:5000"))
    registry_network: str = field(default_factory=lambda: _env("REGISTRY_NETWORK", "kind"))
    registry_insecure: bool = field(default_factory=lambda: _env("REGISTRY_INSECURE", "true").lower() == "true")
    registry_user: str = field(default_factory=lambda: _env("REGISTRY_USER", ""))
    registry_password: str = field(default_factory=lambda: _env("REGISTRY_PASSWORD", ""))

    # Cluster kubeconfig path mounted for deployment runs
    kubeconfig_path: str = field(default_factory=lambda: _env("KUBECONFIG_PATH", "/kube/config"))

    # GitOps: tenant workloads reconciled by ArgoCD instead of applied once.
    #
    # Off by default, and the deploy path still does a direct `kubectl apply`
    # when it is off. Turning it on without a reachable manifest repository
    # would fail every deployment at the publish step, so this stays opt-in
    # rather than defaulting to a mode that needs infrastructure to exist.
    gitops_enabled: bool = field(default_factory=lambda: _env("GITOPS_ENABLED", "false").lower() == "true")
    # The platform's OWN manifest repository — never a tenant's. Only rendered
    # manifests are committed here; tenant source code never touches it.
    gitops_repo_url: str = field(default_factory=lambda: _env("GITOPS_REPO_URL", ""))
    # How ArgoCD addresses that same repository from inside the cluster. The
    # worker pushes from a sandbox on the host's docker network and cannot
    # reach a ClusterIP; ArgoCD is in-cluster and cannot reach a NodePort by
    # the host's name. Same split as REGISTRY / REGISTRY_INTERNAL. Defaults to
    # the worker's URL so a single-address setup still works.
    gitops_repo_url_internal: str = field(default_factory=lambda: _env("GITOPS_REPO_URL_INTERNAL", ""))
    gitops_branch: str = field(default_factory=lambda: _env("GITOPS_BRANCH", "main"))
    gitops_username: str = field(default_factory=lambda: _env("GITOPS_USERNAME", "controlplane"))
    gitops_password: str = field(default_factory=lambda: _env("GITOPS_PASSWORD", ""))

    # Tekton: tenant builds run as Pods in the tenant's own namespace instead
    # of in a sandbox container on the control-plane host.
    #
    # Off by default, and deliberately so: switching it on replaces the runner
    # that enforces the no-docker-socket and no-secret-in-argv guarantees with
    # one whose equivalents are Kubernetes objects that have to exist first
    # (the Pipeline, the registry Secret, Tekton itself). A default of "on"
    # would break every deployment on a cluster that has none of them.
    tekton_enabled: bool = field(default_factory=lambda: _env("TEKTON_ENABLED", "false").lower() == "true")
    # Replaces SANDBOX_* wall-clock limits, which are docker flags and do not
    # apply to a Pod.
    tekton_timeout: str = field(default_factory=lambda: _env("TEKTON_TIMEOUT", "30m"))

    # Elasticsearch / Kibana, for the per-tenant log view.
    #
    # Tenancy is expressed in the index name (tenant-<namespace>-*) because
    # index-pattern privileges are the only enforcement the basic licence has —
    # filtering rows inside a shared index is document-level security, which is
    # Platinum. See core/elk_tenancy.py.
    elasticsearch_url: str = field(default_factory=lambda: _env("ELASTICSEARCH_URL", ""))
    kibana_url: str = field(default_factory=lambda: _env("KIBANA_URL", ""))
    elasticsearch_user: str = field(default_factory=lambda: _env("ELASTICSEARCH_USER", "elastic"))
    elasticsearch_password: str = field(default_factory=lambda: _env("ELASTICSEARCH_PASSWORD", ""))

    # Caps (mirror docs/PLATFORM_SPEC.md §6.2)
    max_projects_per_user: int = field(default_factory=lambda: _env_int("MAX_PROJECTS_PER_USER", 3))
    max_nodes_per_project: int = field(default_factory=lambda: _env_int("MAX_NODES_PER_PROJECT", 10))
    max_total_vcpu: int = field(default_factory=lambda: _env_int("MAX_TOTAL_VCPU", 24))
    max_total_memory_mb: int = field(default_factory=lambda: _env_int("MAX_TOTAL_MEMORY_MB", 49152))
    # Dedicated-cluster-per-tenant (multi-tenancy Phase 3): each VM-mode
    # project is a real 3-node cluster on this host, unlike namespace mode
    # which just carves up one shared cluster. The host has a hard ceiling
    # on how many of those it can run concurrently — provisioning must queue
    # and refuse politely (429) rather than thrash the host once it's hit.
    max_concurrent_vm_clusters: int = field(
        default_factory=lambda: _env_int("MAX_CONCURRENT_VM_CLUSTERS", 2)
    )

    # Rate limits (per §7.6)
    login_rate_per_minute: int = field(default_factory=lambda: _env_int("LOGIN_RATE_PER_MINUTE", 5))
    provision_per_hour: int = field(default_factory=lambda: _env_int("PROVISION_PER_HOUR", 10))
    scans_per_hour: int = field(default_factory=lambda: _env_int("SCANS_PER_HOUR", 60))
    team_invites_per_hour: int = field(default_factory=lambda: _env_int("TEAM_INVITES_PER_HOUR", 20))

    # OIDC single sign-on (docs/TODO.md Task 3.3). The whole flow is opt-in:
    # with oidc_enabled false (default) the SSO endpoints answer 404 and local
    # password auth is untouched, so existing installations keep working.
    oidc_enabled: bool = field(default_factory=lambda: _env("AUTH_OIDC_ENABLED", "false").lower() == "true")
    oidc_issuer: str = field(default_factory=lambda: _env("OIDC_ISSUER"))
    oidc_client_id: str = field(default_factory=lambda: _env("OIDC_CLIENT_ID"))
    oidc_client_secret: str = field(default_factory=lambda: _env("OIDC_CLIENT_SECRET"))
    oidc_redirect_uri: str = field(default_factory=lambda: _env("OIDC_REDIRECT_URI"))
    oidc_scope: str = field(default_factory=lambda: _env("OIDC_SCOPE", "openid email profile groups"))
    oidc_group_claim: str = field(default_factory=lambda: _env("OIDC_GROUP_CLAIM", "groups"))
    # Map of IdP group -> platform role. First matching group wins in
    # insertion order; an unrecognised user falls back to "user".
    oidc_role_map: dict = field(
        default_factory=lambda: _env_json(
            "OIDC_ROLE_MAP",
            {"platform-admins": ["admin"], "platform-developers": ["developer"]},
        )
    )
    # Local password auth stays available behind a flag (Task 3.3 step 3).
    # Production SSO-only installs set this to false and disable /auth/login
    # and /auth/register.
    local_auth_enabled: bool = field(default_factory=lambda: _env("AUTH_LOCAL_ENABLED", "true").lower() == "true")
    oidc_flow_ttl_seconds: int = field(default_factory=lambda: _env_int("OIDC_FLOW_TTL_SECONDS", 600))

    # Ephemeral-environment lifecycle (docs/TODO.md Task 2.2)
    default_ttl_hours: int = field(default_factory=lambda: _env_int("DEFAULT_TTL_HOURS", 24))
    # A dedicated cluster is real host capacity held for one tenant; a bounded
    # namespace slice on the shared cluster costs almost nothing. Default VM
    # mode much shorter so an abandoned environment gives its host budget
    # back quickly instead of sitting "ready" for a full day.
    default_vm_ttl_hours: int = field(default_factory=lambda: _env_int("DEFAULT_VM_TTL_HOURS", 4))
    # A hard ceiling on total lifetime, so "extend" cannot be used repeatedly
    # to turn an ephemeral environment into a permanent one.
    max_ttl_hours: int = field(default_factory=lambda: _env_int("MAX_TTL_HOURS", 168))

    # Warm pool sizes keyed by preset name, e.g. WARM_POOL_TARGETS="small=2,medium=1"
    warm_pool_targets: dict[str, int] = field(default_factory=lambda: _env_pool("WARM_POOL_TARGETS"))

    # Cost model (docs/TODO.md Task 5.1). Unit prices per hour, in whatever
    # currency the operator reports in.
    cost_per_vcpu_hour: float = field(default_factory=lambda: _env_float("COST_PER_VCPU_HOUR", 0.012))
    cost_per_gb_ram_hour: float = field(default_factory=lambda: _env_float("COST_PER_GB_RAM_HOUR", 0.004))
    cost_per_gb_disk_hour: float = field(default_factory=lambda: _env_float("COST_PER_GB_DISK_HOUR", 0.0002))
    cost_currency: str = field(default_factory=lambda: _env("COST_CURRENCY", "EUR"))

    @property
    def gitops_repo_url_for_argocd(self) -> str:
        """The repository URL written into every Application.

        Falls back to the worker's URL when no in-cluster address is set, so a
        deployment where both sides share one address needs one variable rather
        than two identical ones. Never the other way round: defaulting the
        worker's push URL to an in-cluster name would produce a push that
        cannot resolve and a deploy that fails at the last step.
        """
        return self.gitops_repo_url_internal or self.gitops_repo_url

    @property
    def is_dev(self) -> bool:
        return self.environment == "dev"

    def _resolve_secret(self, field_name: str, env_key: str, vault_key: str) -> None:
        """Fill a secret setting from the environment or Vault; fail closed.

        §7 item 2: in production, when the environment variable is unset the
        value is read from Vault at ``vault_secrets_path/<vault_key>``. A
        missing value — env or Vault — is a startup error, never a silent
        default. Frozen-dataclass workaround mirrors override_settings.
        """
        value = _env(env_key)
        if not value:
            from controlplane.core.vault import read_config_secret

            value = read_config_secret(vault_key)
        if not value:
            raise SystemExit(
                f"{env_key} is unset and Vault has no secret at "
                f"{self.vault_secrets_path}/{vault_key}. Refusing to start without it."
            )
        object.__setattr__(self, field_name, value)

    def require_secrets(self) -> None:
        """Resolve every operator secret; production fails closed on any gap."""
        if self.is_dev:
            return
        self._resolve_secret("jwt_secret", "JWT_SECRET", "jwt_secret")
        if self.registry_user and not self.registry_password:
            self._resolve_secret("registry_password", "REGISTRY_PASSWORD", "registry_password")
        if self.tf_state_url and not self.tf_state_password:
            self._resolve_secret("tf_state_password", "TF_STATE_PASSWORD", "tf_state_password")
        if self.oidc_enabled and not self.oidc_client_secret:
            self._resolve_secret("oidc_client_secret", "OIDC_CLIENT_SECRET", "oidc_client_secret")

    def require_jwt_secret(self) -> None:
        self.require_secrets()


settings = Settings()
