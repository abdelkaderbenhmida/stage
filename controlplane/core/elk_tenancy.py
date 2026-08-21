"""Per-tenant Elasticsearch access: one role and one user per team.

Why this shape, and not the obvious one: the obvious one is a single index with
a filter per tenant, which in Elasticsearch means document-level security — a
Platinum feature. On the basic licence the only enforcement that exists is
**index-pattern privileges**, so tenancy has to be expressed in the index name.
Logstash therefore writes each tenant namespace to `tenant-<namespace>-*`
(k8s/monitoring/elk/logstash-values.yaml) and a team's role grants exactly the
patterns for the namespaces that team owns.

The rule that must not be broken: a role's index patterns are built from
namespaces derived by ``k8s_namespace`` from project ids the repository layer
already scoped to the team. Nothing here accepts a namespace, an index pattern
or a project id from a request.

Passwords are generated here and stored in Vault, never in PostgreSQL and never
returned by the API — the platform uses the credential on the tenant's behalf.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass

import httpx

from controlplane.core.config import settings
from controlplane.core.validation import k8s_namespace
from controlplane.core.vault import get_secret_store

logger = logging.getLogger("controlplane.elk")

_TIMEOUT = 10.0
# Kibana answers markedly slower than Elasticsearch, especially while it is
# still installing its own index templates after a restart.
_KIBANA_TIMEOUT = 30.0

# Long enough that it is not worth attacking, short enough to fit any password
# policy. Generated, never chosen: a per-tenant credential nobody types does
# not need to be memorable.
_PASSWORD_BYTES = 24

VAULT_KEY = "elasticsearch_password"


class ElkProvisioningError(RuntimeError):
    """The tenant's Elasticsearch access could not be created or updated."""


def role_name(team_id: uuid.UUID) -> str:
    return f"tenant-{team_id.hex[:20]}"


def user_name(team_id: uuid.UUID) -> str:
    # Same string as the role. Elasticsearch keeps users and roles in separate
    # namespaces, so there is no collision, and one name to look up beats two.
    return role_name(team_id)


def space_name(team_id: uuid.UUID) -> str:
    """The team's Kibana space. Spaces are basic-licence, unlike DLS."""
    return f"tenant-{team_id.hex[:20]}"


def index_patterns(project_ids: list[uuid.UUID]) -> list[str]:
    """The index patterns a team may read — one per project namespace.

    Sorted so the rendered role is stable: an unordered list would make every
    provision look like a change and rewrite the role on every deploy.
    """
    return sorted(f"tenant-{k8s_namespace(project_id)}-*" for project_id in project_ids)


def render_role(team_id: uuid.UUID, project_ids: list[uuid.UUID]) -> dict:
    """The Elasticsearch role granting read access to a team's own logs."""
    patterns = index_patterns(project_ids)
    return {
        # No cluster privileges beyond what a search needs. `monitor` is
        # deliberately absent: it exposes cluster-wide index names, which
        # would tell one tenant that another's namespaces exist.
        "cluster": [],
        "indices": [
            {
                "names": patterns,
                # Read-only. A tenant that could write could forge or delete
                # their own audit trail, which is the opposite of why logs are
                # kept.
                "privileges": ["read", "view_index_metadata"],
            }
        ]
        # An empty `names` list is rejected by Elasticsearch, and a role with
        # no indices block is a role that grants nothing — which is the
        # correct state for a team with no projects yet.
        if patterns
        else [],
        "applications": [
            {
                "application": "kibana-.kibana",
                "privileges": ["feature_discover.read"],
                "resources": [f"space:{space_name(team_id)}"],
            }
        ],
    }


def render_space(team_id: uuid.UUID, team_name: str) -> dict:
    return {
        "id": space_name(team_id),
        "name": team_name[:60] or space_name(team_id),
        "description": "Logs for this team's own projects.",
        # Only Discover. A tenant space with the management features enabled
        # would let a tenant edit index patterns and point Discover at
        # somebody else's index — the role would still refuse the read, but
        # the platform should not be handing out the attempt.
        "disabledFeatures": [
            "dev_tools", "management", "monitoring", "ml", "apm", "siem",
            "advancedSettings", "indexPatterns", "savedObjectsManagement",
        ],
    }


@dataclass(frozen=True)
class ElkAdmin:
    """Where and how to reach Elasticsearch and Kibana as an administrator."""

    elasticsearch_url: str
    kibana_url: str
    username: str
    password: str

    @classmethod
    def from_settings(cls) -> ElkAdmin:
        return cls(
            elasticsearch_url=settings.elasticsearch_url.rstrip("/"),
            kibana_url=settings.kibana_url.rstrip("/"),
            username=settings.elasticsearch_user,
            password=settings.elasticsearch_password,
        )

    @property
    def auth(self) -> tuple[str, str]:
        return (self.username, self.password)


def _check(response: httpx.Response, what: str) -> None:
    if response.status_code >= 400:
        raise ElkProvisioningError(f"{what} failed ({response.status_code}): {response.text[:300]}")


def provision_team(
    team_id: uuid.UUID,
    team_name: str,
    project_ids: list[uuid.UUID],
    admin: ElkAdmin | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Create or update a team's Kibana space, role and user. Returns the password.

    Idempotent: called on every provision, because a team that adds a project
    needs the new namespace in its role and the alternative — creating the role
    once — would leave every project after the first invisible to its owner.

    The password is rotated only when the user does not exist yet; re-running
    this must not invalidate a credential the platform has already stored.
    """
    admin = admin or ElkAdmin.from_settings()
    if not admin.elasticsearch_url:
        raise ElkProvisioningError("ELASTICSEARCH_URL is not configured.")

    store = get_secret_store()
    existing = store.get(str(team_id), VAULT_KEY)
    password = existing or secrets.token_urlsafe(_PASSWORD_BYTES)

    owns_client = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        # Elasticsearch first, and this order is load-bearing.
        #
        # The role is the access control; the Kibana space is a place to put a
        # saved search. Doing the space first meant a slow Kibana — its spaces
        # API is not fast on a small cluster — aborted the whole call before
        # the role was updated, so a team that had just added a project
        # silently kept the old role and could not see its own new logs. The
        # cosmetic half must never be able to fail the functional half.
        _check(
            client.put(
                f"{admin.elasticsearch_url}/_security/role/{role_name(team_id)}",
                json=render_role(team_id, project_ids),
                auth=admin.auth,
            ),
            "creating the team's Elasticsearch role",
        )
        _check(
            client.put(
                f"{admin.elasticsearch_url}/_security/user/{user_name(team_id)}",
                json={
                    "password": password,
                    "roles": [role_name(team_id)],
                    "full_name": team_name[:80],
                    "metadata": {"managed_by": "controlplane", "team_id": str(team_id)},
                },
                auth=admin.auth,
            ),
            "creating the team's Elasticsearch user",
        )

        if admin.kibana_url:
            _ensure_space(client, admin, team_id, team_name)
    finally:
        if owns_client:
            client.close()

    if not existing:
        store.set(str(team_id), VAULT_KEY, password)
    return password


def _ensure_space(client: httpx.Client, admin: ElkAdmin, team_id: uuid.UUID, team_name: str) -> None:
    """Create the team's Kibana space, and never fail the caller if it cannot.

    By the time this runs the role and user exist, so the team can already read
    its own logs through the platform. A missing space costs them a tidy
    landing page in Kibana, which is not worth failing a provision over — and
    Kibana is markedly slower to answer than Elasticsearch, so this is the call
    most likely to time out.
    """
    try:
        if not _space_missing(client, admin, team_id):
            return
        response = client.post(
            f"{admin.kibana_url}/api/spaces/space",
            json=render_space(team_id, team_name),
            auth=admin.auth,
            # Kibana rejects unbranded API calls outright; this header is its
            # CSRF guard, not decoration.
            headers={"kbn-xsrf": "controlplane"},
            timeout=_KIBANA_TIMEOUT,
        )
        # 409 means another provision created it first, which is success.
        if response.status_code >= 400 and response.status_code != 409:
            logger.warning(
                "could not create the Kibana space for team %s (HTTP %s)",
                team_id, response.status_code,
            )
    except httpx.HTTPError as exc:
        logger.warning("could not create the Kibana space for team %s: %s", team_id, exc)


def _space_missing(client: httpx.Client, admin: ElkAdmin, team_id: uuid.UUID) -> bool:
    """Whether the team's space still needs creating.

    Kibana's space API has no upsert: POST on an existing id is a 409, and PUT
    on a missing one is a 404. Checking first is the only way to be idempotent.
    """
    response = client.get(
        f"{admin.kibana_url}/api/spaces/space/{space_name(team_id)}",
        auth=admin.auth,
        headers={"kbn-xsrf": "controlplane"},
        timeout=_KIBANA_TIMEOUT,
    )
    return response.status_code == 404
