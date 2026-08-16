"""Per-project kubeconfig storage (multi-tenancy plan, Phase 3).

Dedicated-cluster-per-tenant means every VM-mode project has its own API
server and its own admin credential. That credential is exactly as sensitive
as a Vault root token would have been under the old shared-cluster model, so
it lives in the same secret store as SSH keys and registry credentials —
never in Postgres, never on disk outside the sandbox that needs it.

Reuses ``core.vault.SecretStore`` keyed by ``str(project_id)`` instead of a
user id; the store itself is user/tenant-agnostic, it just namespaces by
whatever key it is given.
"""

import uuid

from controlplane.core.vault import get_secret_store

_KEY = "kubeconfig"


def store_kubeconfig(project_id: uuid.UUID, kubeconfig_yaml: str) -> None:
    get_secret_store().set(str(project_id), _KEY, kubeconfig_yaml)


def get_kubeconfig(project_id: uuid.UUID) -> str | None:
    return get_secret_store().get(str(project_id), _KEY)


def delete_kubeconfig(project_id: uuid.UUID) -> None:
    get_secret_store().delete(str(project_id), _KEY)


def transfer_kubeconfig(from_id: uuid.UUID, to_id: uuid.UUID) -> None:
    """Move a warm-pool cluster's credential to the project that claimed it."""
    kubeconfig = get_kubeconfig(from_id)
    if kubeconfig is None:
        return
    store_kubeconfig(to_id, kubeconfig)
    delete_kubeconfig(from_id)
