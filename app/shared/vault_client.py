"""Vault client utility — shared by all microservices to fetch secrets
dynamically from HashiCorp Vault at startup.

Usage in a service:
    from shared.vault_client import get_secret, vault_health

    database_url = get_secret("DATABASE_URL")          # raises if missing
    jwt_key      = get_secret("JWT_SECRET_KEY")

Fail-fast behavior (fixes devops-analysis-report.md P0 #3):
    By default, `get_secret` RAISES SecretUnavailable when the secret cannot
    be resolved from Vault. This is a deliberate fail-closed design: a service
    should never silently start with a fake fallback secret in production.

    For non-sensitive environment overrides (e.g. a default SQLite path used
    ONLY in local development), pass `default=...` explicitly. Any default
    starting with `dev-` or `sqlite:` is logged as a development value.

Configuration (via env vars):
    VAULT_ADDR         required. e.g. http://vault-service.vault.svc.cluster.local:8200
    VAULT_TOKEN        required when not using Kubernetes auth (dev mode).
                       In production, prefer the Vault Agent Injector or
                       Kubernetes auth method managed by the platform.
    SERVICE_NAME       required. Selects the secret path (secret/devops-platform/<service>).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Dict, Optional

import hvac

# Shared structured logger — see shared/logging.py.
_LOG = logging.getLogger(os.environ.get("SERVICE_NAME", "vault-client"))


class SecretUnavailable(RuntimeError):
    """Raised when a required secret cannot be fetched from Vault or env.

    Fail-closed: never fall back to an opaque placeholder value silently —
    surface the missing secret so the operator fixes Vault / RBAC, not the
    user (whose JWT would otherwise be signed with an attacker-known key).
    """


def _vault_addr() -> str:
    addr = os.environ.get("VAULT_ADDR")
    if not addr:
        raise SecretUnavailable(
            "VAULT_ADDR is not set. Configure the deployment to point at the "
            "Vault service (e.g. http://vault-service.vault.svc.cluster.local:8200)."
        )
    return addr


def _vault_token() -> str:
    """Resolve Vault token.

    Resolution order (production-safe):
      1. /vault/secrets/token (Vault Agent Injector mounts a short-lived token
         for this pod when `vault.hashicorp.com/agent-inject` annotation is
         set on the deployment + the SA is bound to a Kubernetes auth role).
      2. VAULT_TOKEN env var (legacy/dev path — discouraged in production).
      3. VAULT_DEV_ROOT_TOKEN_ID (Vault dev-mode fallback only).
    """
    injector_path = "/vault/secrets/token"
    if os.path.exists(injector_path):
        try:
            token = open(injector_path).read().strip()
            if token:
                return token
        except OSError as exc:
            _LOG.warning(
                "vault.injector_token_unreadable",
                extra={"event": "vault.injector_token_unreadable", "error": str(exc)},
            )
    token = os.environ.get("VAULT_TOKEN") or os.environ.get("VAULT_DEV_ROOT_TOKEN_ID")
    if not token:
        raise SecretUnavailable(
            "No Vault token available. Configure the Vault Agent Injector "
            "annotations in the Deployment + bind the ServiceAccount to "
            "auth/kubernetes/config, OR set VAULT_TOKEN in dev mode."
        )
    return token


def _service_name() -> str:
    name = os.environ.get("SERVICE_NAME")
    if not name:
        raise SecretUnavailable("SERVICE_NAME is not set; cannot build Vault secret path.")
    return name


def _secrets_path() -> str:
    return f"secret/data/devops-platform/{_service_name()}"


def _is_vault_configured() -> bool:
    # Prefer real Vault client when VAULT_ADDR+VAULT_TOKEN are set. The
    # Vault Agent Injector mounts the per-pod short-lived token at
    # `/vault/secrets/token` (default) when the
    # `vault.hashicorp.com/agent-inject` annotation is enabled.
    env_token = (
        os.environ.get("VAULT_TOKEN")
        or os.environ.get("VAULT_DEV_ROOT_TOKEN_ID")
    )
    injector_token_path = "/vault/secrets/token"
    injector_token = None
    if os.path.exists(injector_token_path):
        try:
            injector_token = open(injector_token_path).read().strip() or None
        except OSError:
            injector_token = None
    return bool(os.environ.get("VAULT_ADDR")) and bool(env_token or injector_token)


@lru_cache(maxsize=1)
def _fetch_all_secrets() -> Dict[str, str]:
    """Fetch all secrets for this service from Vault. Cached for process lifetime.

    Raises SecretUnavailable if Vault is unreachable or auth fails — fail closed.
    Use reload_secrets() to reset the cache after rotating tokens.
    """
    if not _is_vault_configured():
        raise SecretUnavailable(
            "Vault is not configured (VAULT_ADDR or token missing). Required secrets "
            "cannot be fetched — refusing to fall back to defaults."
        )

    try:
        client = hvac.Client(url=_vault_addr(), token=_vault_token())
        if not client.is_authenticated():
            raise SecretUnavailable(
                "Vault authentication failed: token rejected. Check the vault-root-token "
                "Secret or the Kubernetes auth role for this ServiceAccount."
            )

        _LOG.info(
            "vault.fetch", extra={"event": "vault.fetch", "path": _secrets_path()}
        )
        response = client.read(_secrets_path())
        if response is None:
            raise SecretUnavailable(
                f"Secret path {_secrets_path()!r} does not exist in Vault. "
                f"Run the vault-setup Job (k8s/vault/manifests.yaml) to seed it."
            )

        return response["data"]["data"]

    except SecretUnavailable:
        raise
    except Exception as exc:
        # Fail closed — do NOT return {} so callers cannot silently fall back.
        _LOG.error(
            "vault.fetch_failed",
            extra={"event": "vault.fetch_failed", "error": str(exc), "url": _vault_addr()},
        )
        raise SecretUnavailable(
            f"Could not reach Vault at {_vault_addr()}: {exc}. Refusing to start."
        ) from exc


def get_secret(name: str, default: Optional[str] = None) -> str:
    """Fetch a secret by name. Resolution order:
      1. Vault secret path for this service (fails closed on Vault error)
      2. Environment variable of the same name (allowed for development overrides)
      3. Default argument (only for non-sensitive dev values — logged as dev)

    Raises SecretUnavailable if the secret cannot be resolved from any source
    and no `default` is provided. Always log when a default is used so dev
    fallback is visible in production logs.
    """
    try:
        secrets = _fetch_all_secrets()
    except SecretUnavailable:
        # If an env override exists, use it — this is the normal local-dev path.
        env_value = os.environ.get(name)
        if env_value is not None:
            _LOG.warning(
                "secret.from_env",
                extra={"event": "secret.from_env", "secret_name": name, "reason": "vault_unreachable"},
            )
            return env_value
        if default is not None:
            _LOG.warning(
                "secret.default_used",
                extra={"event": "secret.default_used", "secret_name": name},
            )
            return default
        raise

    if name in secrets:
        return secrets[name]

    env_value = os.environ.get(name)
    if env_value is not None:
        return env_value

    if default is not None:
        _LOG.warning(
            "secret.default_used",
            extra={"event": "secret.default_used", "secret_name": name},
        )
        return default

    raise SecretUnavailable(
        f"Secret {name!r} not found in Vault path {_secrets_path()!r} or environment."
    )


def vault_health() -> Dict[str, bool]:
    """Check Vault reachability for readiness probes.

    Lightweight: uses the cached client if available, never raises. Returns
    a dict suitable for JSON-serialization in /readyz responses. Used by the
    service readiness probe — NOT a substitute for production liveness.
    """
    if not _is_vault_configured():
        return {"configured": False, "reachable": False, "reachable_in_last_call": False}
    try:
        client = hvac.Client(url=_vault_addr(), token=_vault_token())
        return {
            "configured": True,
            "reachable": bool(client.is_authenticated()),
        }
    except Exception as exc:
        _LOG.warning(
            "vault.health_failed",
            extra={"event": "vault.health_failed", "error": str(exc)},
        )
        return {"configured": True, "reachable": False, "error": str(exc)}


def reload_secrets() -> None:
    """Clear the secret cache. Used in tests / after token rotation."""
    _fetch_all_secrets.cache_clear()
