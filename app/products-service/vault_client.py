"""
Vault client utility — shared by all microservices to fetch secrets dynamically
from HashiCorp Vault at startup.

Usage in a service:
    from vault_client import get_secret

    database_url = get_secret("DATABASE_URL")
    jwt_key      = get_secret("JWT_SECRET_KEY")

Behavior:
  1. If VAULT_ADDR is set and Vault is reachable, fetches secrets from the
     service-specific path (secret/devops-platform/<service-name>).
  2. Otherwise, falls back to environment variables of the same name.
     This allows local development without Vault.

Configuration (all optional, via env vars):
    VAULT_ADDR   — e.g. http://vault-service.vault.svc.cluster.local:8200
    VAULT_TOKEN  — Vault token (dev: root-token-change-me)
    SERVICE_NAME — used to select the secret path (set by the deployment)
"""

import os
from functools import lru_cache
from typing import Dict, Optional

import hvac


def _vault_addr() -> str:
    return os.environ.get("VAULT_ADDR", "http://vault-service.vault.svc.cluster.local:8200")


def _vault_token() -> str:
    # In a real deployment, use Kubernetes auth (jwt token) instead of root token.
    return os.environ.get("VAULT_TOKEN", os.environ.get("VAULT_DEV_ROOT_TOKEN_ID", ""))


def _service_name() -> str:
    return os.environ.get("SERVICE_NAME", "unknown-service")


def _secrets_path() -> str:
    return f"secret/data/devops-platform/{_service_name()}"


def _is_vault_configured() -> bool:
    return bool(_vault_token()) and bool(os.environ.get("VAULT_ADDR"))


@lru_cache(maxsize=1)
def _fetch_all_secrets() -> Dict[str, str]:
    """Fetch all secrets for this service from Vault. Cached for the process lifetime."""
    if not _is_vault_configured():
        return {}

    try:
        client = hvac.Client(url=_vault_addr(), token=_vault_token())
        if not client.is_authenticated():
            raise RuntimeError("Vault authentication failed (token rejected)")

        response = client.read(_secrets_path())
        if response is None:
            # Secret path doesn't exist in Vault — return empty dict
            return {}

        return response["data"]["data"]

    except Exception as exc:
        # If Vault is unreachable, fall back to env vars silently
        print(f"[vault_client] Warning: could not reach Vault at {_vault_addr()}: {exc}")
        return {}


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Fetch a secret by name. Resolution order:
      1. Vault secret path for this service
      2. Environment variable of the same name
      3. Default argument
    """
    secrets = _fetch_all_secrets()
    if name in secrets:
        return secrets[name]

    return os.environ.get(name, default)


def reload_secrets() -> None:
    """Clear the secret cache (used in tests / long-lived processes)."""
    _fetch_all_secrets.cache_clear()
