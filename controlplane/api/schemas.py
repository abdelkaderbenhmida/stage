import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from controlplane.schemas.spec import InfraSpec


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    password_confirm: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    vcpu: int
    memory_mb: int
    disk_gb: int
    role: str
    ip_address: str | None
    status: str
    last_seen_at: datetime | None


class ProjectCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9-]{3,30}$")
    description: str | None = None
    # Exactly one of infra_spec / preset. A preset is expanded server-side into
    # a full InfraSpec and then validated identically — it is a convenience,
    # not a way around validation (docs/TODO.md Task 2.1).
    infra_spec: InfraSpec | None = None
    preset: Literal["small", "medium", "large"] | None = None
    team_id: uuid.UUID | None = None
    ttl_hours: int | None = Field(default=None, ge=0, le=168)
    auto_destroy: bool = True
    mode: Literal["namespace", "vm"] | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> "ProjectCreate":
        if (self.infra_spec is None) == (self.preset is None):
            raise ValueError(
                "Provide exactly one of 'infra_spec' or 'preset' — "
                "'infra_spec' describes nodes individually, 'preset' picks a "
                "standard size."
            )
        return self


class ProjectPatch(BaseModel):
    description: str | None = None
    infra_spec: InfraSpec | None = None


class ExtendRequest(BaseModel):
    hours: int = Field(ge=1, le=168)


class ProjectOut(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    team_id: uuid.UUID | None = None
    name: str
    description: str | None
    status: str
    infra_spec: dict
    workspace_path: str | None
    created_at: datetime
    updated_at: datetime
    nodes: list[NodeOut] = []
    ttl_hours: int | None = None
    expires_at: datetime | None = None
    auto_destroy: bool = True
    expiry_warned: bool = False


class ProjectListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    status: str
    created_at: datetime
    expires_at: datetime | None = None
    auto_destroy: bool = True
    expiry_warned: bool = False


# --------------------------------------------------------------------- teams


class TeamCreate(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    description: str | None = None


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    is_personal: bool
    created_at: datetime


class TeamMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str


class TeamMemberCreate(BaseModel):
    email: EmailStr
    role: Literal["viewer", "developer", "owner", "admin"] = "developer"


# ----------------------------------------------------------- cost + catalogue


class GitCredentialIn(BaseModel):
    """A token the platform may use to clone this team's private repositories.

    Prefer a GitHub App installation token (``ghs_``), which expires in an hour
    and covers only the repositories the team selected. A fine-grained PAT is
    accepted but is long-lived, so scope it to ``contents: read`` on specific
    repositories.
    """

    token: str = Field(min_length=8, max_length=500)
    # GitHub ignores the username when the password is a token; this default is
    # what it documents for installation tokens and is harmless for a PAT.
    username: str = Field(default="x-access-token", min_length=1, max_length=100)


class GitCredentialStatus(BaseModel):
    """Whether a credential exists. Never carries the value."""

    configured: bool
    # Whether it is held in a real secret manager. Reported so the interface
    # cannot tell someone their token is protected while it sits in plaintext.
    encrypted: bool = True


class CostOut(BaseModel):
    currency: str
    total: float
    projects: list[dict]


class CatalogueEntry(BaseModel):
    deployment_id: uuid.UUID
    service_name: str
    project_id: uuid.UUID
    project_name: str
    team_id: uuid.UUID | None
    status: str
    live_url: str | None
    branch: str
    updated_at: datetime
    critical: int = 0
    high: int = 0
    # The most recent job (built/scan/undeploy) for this deployment, so the
    # catalogue can link straight to its logs (docs/TODO.md Task 5.3).
    logs_job_id: uuid.UUID | None = None
    owner_email: str | None = None


# ------------------------------------------------------------------ webhooks


class WebhookSubscriptionCreate(BaseModel):
    branch: str = "main"
    provider: Literal["github", "gitlab"] = "github"
    pull_request_number: int | None = None


class WebhookSubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    deployment_id: uuid.UUID
    provider: str
    repo_url: str
    branch: str
    active: bool
    created_at: datetime


class WebhookSecretOut(WebhookSubscriptionOut):
    # Returned only once, at creation: the secret is never readable again, so
    # a leaked listing response cannot be replayed to forge deliveries.
    secret: str


class DestroyRequest(BaseModel):
    confirm_name: str


class DeploymentCreate(BaseModel):
    service_name: str = Field(pattern=r"^[a-z0-9-]{1,60}$")
    repo_url: str
    branch: str = "main"
    port: int = Field(ge=1, le=65535)
    replicas: int = Field(default=2, ge=1, le=10)
    # docs/TODO.md Task 5.2: an Argo Rollouts strategy ("canary"/"bluegreen")
    # replaces the plain Deployment rollout and gates on the SLO analysis.
    strategy: Literal["deployment", "canary", "bluegreen"] = "deployment"

    # Non-secret configuration, handed to the container as environment
    # variables. Without it the platform can only run applications that need
    # no configuration at all.
    env: dict[str, str] = Field(default_factory=dict)
    # Secret configuration. The values are stored in the secret store and
    # never persisted on the deployment row, so they cannot be read back out
    # through this API.
    secrets: dict[str, str] = Field(default_factory=dict)
    # The path the readiness and liveness probes call. It was hardcoded to
    # /livez, which quietly required every application to implement that
    # exact endpoint.
    health_path: str = Field(default="/livez", pattern=r"^/[A-Za-z0-9\-._~/]{0,190}$")


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    service_name: str
    repo_url: str
    branch: str
    port: int
    image_ref: str | None
    status: str
    live_url: str | None
    env_vars: dict = Field(default_factory=dict)
    # Names only — the values are in the secret store and no endpoint returns
    # them.
    secret_keys: list = Field(default_factory=list)
    health_path: str = "/livez"
    replicas: int
    strategy: str = "deployment"
    created_at: datetime


class ScanRequest(BaseModel):
    tool: Literal["trivy", "gitleaks", "pip_audit", "all"]
    target: str = Field(min_length=3, max_length=2048)


class ScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    deployment_id: uuid.UUID | None
    tool: str
    target: str
    status: str
    summary: dict | None
    duration_seconds: int | None
    created_at: datetime


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scan_id: uuid.UUID
    severity: str
    identifier: str | None
    package_name: str | None
    installed_version: str | None
    fixed_version: str | None
    title: str | None
    file_path: str | None
    line_number: int | None


class FindingsPage(BaseModel):
    items: list[FindingOut]
    total: int
    page: int


class SecuritySummaryOut(BaseModel):
    project_id: uuid.UUID
    current: dict
    trend: list[dict]
    top_issues: list[dict]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    deployment_id: uuid.UUID | None
    type: str
    status: str
    log: str
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None


class JobSummaryOut(BaseModel):
    """A run in a deployment's history — everything but the log.

    The log stays behind `GET /jobs/{id}`: history pages are read far more
    often than logs, and each one can be 200 kB.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    deployment_id: uuid.UUID | None
    type: str
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None


class Message(BaseModel):
    message: str


class GraphFanoutNode(BaseModel):
    """One expanded leg of a collapsed matrix node."""

    id: str
    label: str
    status: str
    duration_s: float | None = None
    url: str | None = None


class GraphNode(BaseModel):
    """One box in a pipeline graph.

    `status` uses the shared six-value vocabulary (pending, running,
    succeeded, failed, skipped, cancelled). Edges live on the node as
    `depends_on`, not in a separate list — one source of truth, and the
    renderer derives edges from it in one line.
    """

    id: str
    label: str
    status: str
    depends_on: list[str] = []
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None
    detail: str = ""
    url: str | None = None
    fanout: list[GraphFanoutNode] = []


class PipelineGraphOut(BaseModel):
    """The normalised pipeline-graph contract shared by every producer.

    Versioned so a renderer can reject or adapt to a changed shape.
    """

    version: str = "pipeline-graph/1"
    source: Literal["job", "ci", "service"]
    title: str
    subtitle: str = ""
    status: str
    url: str | None = None
    degraded: bool = False
    degraded_reason: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: str = ""
    nodes: list[GraphNode] = []
