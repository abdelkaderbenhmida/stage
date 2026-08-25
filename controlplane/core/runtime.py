"""Runtime wiring: workspace paths, Terraform/Ansible runtime configs from the
control plane's own host settings and the user's Vault secrets."""

import uuid
from pathlib import Path

from controlplane.core.config import settings
from controlplane.core.vault import get_secret_store
from controlplane.renderers import AnsibleRuntimeConfig, TerraformRuntimeConfig


def project_workspace(project_id: uuid.UUID) -> Path:
    """Workspace for a VM-mode project: Terraform state + Ansible runs."""
    return Path(settings.workspace_root) / str(project_id)


def namespace_workspace(project_id: uuid.UUID) -> Path:
    """Workspace for a namespace-mode project. There is no Terraform state to
    keep, so the artifacts live under their own root instead of pretending a
    ``project_workspace`` exists (docs/TODO.md §8 item 1)."""
    return Path(settings.workspace_root) / "namespaces" / str(project_id)


def deployment_manifests_dir(project_id: uuid.UUID, mode: str, deployment_id: uuid.UUID) -> Path:
    """Where rendered deployment manifests land, kept inside the project's
    workspace. Namespace-mode projects get the namespace root; VM-mode keeps
    the historical `workspace/manifests` layout.

    Scoped by deployment_id, not just project_id: two services in one
    project deploying at the same time used to render into the exact same
    `manifests/deployment.yaml` etc, and whichever job's `kubectl apply` ran
    second read whatever the other job had written last — reproduced live
    deploying six services at once, where one service's rollout applied
    under a completely different service's name and failed with
    "deployments.apps '<other-service>' already exists".
    """
    base = namespace_workspace(project_id) if mode == "namespace" else project_workspace(project_id)
    return base / "manifests" / str(deployment_id)


def terraform_runtime(user_id: uuid.UUID) -> TerraformRuntimeConfig:
    store = get_secret_store()
    public_key = store.get(str(user_id), "ssh_public_key") or ""
    return TerraformRuntimeConfig(
        libvirt_uri=settings.libvirt_uri,
        storage_pool=settings.storage_pool,
        base_image_path=settings.base_image_path,
        dns_servers=settings.dns_servers,
        network_interface=settings.network_interface,
        ssh_user="devops",
        ssh_public_key=public_key,
        libvirt_volume_owner_uid=settings.libvirt_volume_owner_uid,
        libvirt_volume_group_gid=settings.libvirt_volume_group_gid,
        state_url=settings.tf_state_url,
        state_username=settings.tf_state_username,
        state_password=settings.tf_state_password,
        state_insecure=settings.tf_state_insecure,
    )


def ansible_runtime() -> AnsibleRuntimeConfig:
    return AnsibleRuntimeConfig(ssh_user="devops")


def user_ssh_private_key(user_id: uuid.UUID) -> str | None:
    return get_secret_store().get(str(user_id), "ssh_private_key")
