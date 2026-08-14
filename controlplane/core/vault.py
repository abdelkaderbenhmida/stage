"""Per-user secret store backed by Vault (docs/PLATFORM_SPEC.md §7.4).

Secrets (SSH keypairs, registry credentials) are keyed by user ID and never
touch PostgreSQL. In ``ENVIRONMENT=dev`` an ephemeral in-memory store is used
so the control plane runs without a Vault server.
"""

import logging

import hvac

from controlplane.core.config import settings

logger = logging.getLogger("controlplane.vault")

_DEV_STORE: dict[str, dict[str, str]] = {}


class SecretStore:
    def set(self, user_id: str, key: str, value: str) -> None:
        raise NotImplementedError

    def get(self, user_id: str, key: str) -> str | None:
        raise NotImplementedError

    def delete(self, user_id: str, key: str) -> None:
        raise NotImplementedError


class DevSecretStore(SecretStore):
    def set(self, user_id: str, key: str, value: str) -> None:
        _DEV_STORE.setdefault(user_id, {})[key] = value

    def get(self, user_id: str, key: str) -> str | None:
        return _DEV_STORE.get(user_id, {}).get(key)

    def delete(self, user_id: str, key: str) -> None:
        _DEV_STORE.get(user_id, {}).pop(key, None)


class VaultSecretStore(SecretStore):
    def __init__(self) -> None:
        self._client = hvac.Client(url=settings.vault_addr, token=settings.vault_token)

    def set(self, user_id: str, key: str, value: str) -> None:
        if settings.vault_kv_version == "2":
            self._client.secrets.kv.v2.create_or_update_secret(
                path=f"controlplane/{user_id}/{key}", secret={"value": value}
            )
        else:
            self._client.write(f"controlplane/{user_id}/{key}", value=value)

    def get(self, user_id: str, key: str) -> str | None:
        try:
            if settings.vault_kv_version == "2":
                response = self._client.secrets.kv.v2.read_secret_version(
                    path=f"controlplane/{user_id}/{key}"
                )
                return response["data"]["data"]["value"]
            response = self._client.read(f"controlplane/{user_id}/{key}")
            return (response or {}).get("data", {}).get("value")
        except Exception:
            return None

    def delete(self, user_id: str, key: str) -> None:
        try:
            if settings.vault_kv_version == "2":
                self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                    path=f"controlplane/{user_id}/{key}"
                )
            else:
                self._client.delete(f"controlplane/{user_id}/{key}")
        except Exception:
            logger.warning("vault.delete_failed user=%s key=%s", user_id, key)


_secret_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    global _secret_store
    if _secret_store is None:
        if settings.is_dev or not settings.vault_addr:
            _secret_store = DevSecretStore()
        else:
            _secret_store = VaultSecretStore()
    return _secret_store


def read_config_secret(key: str) -> str | None:
    """Read one control-plane *configuration* secret from Vault.

    Section 7 item 2: operator secrets like the JWT signing key must live in
    Vault, not in ``docker-compose.yml``. This reads the ``<key>`` entry under
    ``settings.vault_secrets_path`` (KV v2 value or plain data map). Returns
    None when the path does not exist; any authentication or permission
    failure raises SystemExit — fail closed, never silently start without
    secrets.
    """
    if settings.is_dev or not settings.vault_addr:
        return None
    client = hvac.Client(url=settings.vault_addr, token=settings.vault_token)
    if not client.is_authenticated():
        raise SystemExit(
            "Vault authentication failed; refusing to start without configured secrets."
        )
    path = f"{settings.vault_secrets_path}/{key}"
    try:
        if settings.vault_kv_version == "2":
            response = client.secrets.kv.v2.read_secret_version(path=path)
            data = response["data"]["data"]
            if isinstance(data, dict):
                return data.get("value") or data.get("data")
            return str(data) if data else None
        response = client.read(path)
        return (response or {}).get("data", {}).get("value")
    except hvac.exceptions.InvalidPath:
        return None
    except hvac.exceptions.Forbidden as exc:
        raise SystemExit(
            f"Vault denied read of {path}; the control-plane token needs read there."
        ) from exc
