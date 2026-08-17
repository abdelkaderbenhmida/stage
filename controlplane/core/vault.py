"""Per-user secret store backed by Vault (docs/PLATFORM_SPEC.md §7.4).

Secrets (SSH keypairs, registry credentials) are keyed by user ID and never
touch PostgreSQL. In ``ENVIRONMENT=dev`` a Redis-backed store is used so the
control plane runs without a Vault server.
"""

import logging

import hvac
import redis as redis_lib

from controlplane.core.config import settings

logger = logging.getLogger("controlplane.vault")


class SecretStore:
    def set(self, user_id: str, key: str, value: str) -> None:
        raise NotImplementedError

    def get(self, user_id: str, key: str) -> str | None:
        raise NotImplementedError

    def delete(self, user_id: str, key: str) -> None:
        raise NotImplementedError


class DevSecretStore(SecretStore):
    """Backed by Redis rather than an in-process dict: the API process (which
    mints secrets, e.g. the SSH keypair at registration) and the worker
    process (which reads them at provision time) are always separate OS
    processes, even in dev — an in-memory dict is invisible across that
    boundary, so provisioning could never find the SSH key it needs."""

    def __init__(self) -> None:
        self._client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)

    def _redis_key(self, user_id: str, key: str) -> str:
        return f"controlplane:dev-secrets:{user_id}:{key}"

    def set(self, user_id: str, key: str, value: str) -> None:
        self._client.set(self._redis_key(user_id, key), value)

    def get(self, user_id: str, key: str) -> str | None:
        return self._client.get(self._redis_key(user_id, key))

    def delete(self, user_id: str, key: str) -> None:
        self._client.delete(self._redis_key(user_id, key))


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
        # Configuring Vault is an explicit act, so honour it whichever
        # environment this is. The condition used to be "dev OR no Vault",
        # which meant a developer who had deliberately pointed the platform at
        # a real Vault still got the plaintext Redis store and no indication
        # that their secrets were not going where they had configured.
        if settings.vault_addr:
            _secret_store = VaultSecretStore()
        else:
            _secret_store = DevSecretStore()
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
