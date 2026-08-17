"""Configuration handed to a tenant's running container.

Two kinds, deliberately kept apart:

**Environment variables** are ordinary configuration — a log level, a feature
flag, a public URL. They live on the Deployment row, are shown back to the
tenant, and end up in the rendered manifest.

**Secrets** are credentials. Only their *names* are stored on the row; the
values go to the secret store (Vault) under a key scoped to the deployment.
A database row is the wrong place for a tenant's database password: it is
readable by anything that can see the deployment, it lands in every backup in
plaintext, and it would be echoed straight back by the deployments API.

The two share one namespace inside the container, so a name cannot be both —
otherwise which one wins would depend on manifest ordering.
"""

from __future__ import annotations

import re

from controlplane.core.vault import get_secret_store

# POSIX-ish: letters, digits, underscore, not starting with a digit. Kubernetes
# accepts more, but anything outside this is either unusable from a shell or a
# sign of a mistake, and a permissive name is one more thing to escape.
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Names the platform sets itself. Letting a tenant override PORT would break
# the readiness probe, which targets the port the platform assigned.
RESERVED = frozenset({"PORT", "HOSTNAME", "KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT"})

MAX_VARS = 100
MAX_VALUE = 4096

_SECRET_KEY_PREFIX = "deployment_env"


class ConfigError(ValueError):
    """A configuration the platform will not accept."""


def validate_env(env: dict | None, *, kind: str = "environment variable") -> dict[str, str]:
    """Check names and values, returning a clean copy."""
    if not env:
        return {}
    if not isinstance(env, dict):
        raise ConfigError(f"{kind}s must be given as a name/value object.")
    if len(env) > MAX_VARS:
        raise ConfigError(f"Too many {kind}s (limit {MAX_VARS}).")

    clean: dict[str, str] = {}
    for name, value in env.items():
        if not isinstance(name, str) or not _NAME.match(name):
            raise ConfigError(
                f"{name!r} is not a usable {kind} name — use letters, digits and "
                "underscores, not starting with a digit."
            )
        if name in RESERVED:
            raise ConfigError(f"{name} is set by the platform and cannot be overridden.")
        if value is None:
            raise ConfigError(f"{name} has no value.")
        text = str(value)
        if len(text) > MAX_VALUE:
            raise ConfigError(f"{name} is too long (limit {MAX_VALUE} characters).")
        if "\x00" in text:
            raise ConfigError(f"{name} contains a null byte.")
        clean[name] = text
    return clean


def assert_disjoint(env: dict, secrets: dict) -> None:
    """A name may be configuration or a secret, never both.

    They share one environment inside the container, so allowing both would
    make the winner depend on the order the manifest happens to list them.
    """
    clash = sorted(set(env) & set(secrets))
    if clash:
        raise ConfigError(
            f"{', '.join(clash)} given as both an environment variable and a secret — "
            "pick one."
        )


def _key(deployment_id) -> str:
    return f"{_SECRET_KEY_PREFIX}:{deployment_id}"


def store_secrets(team_id, deployment_id, secrets: dict[str, str]) -> list[str]:
    """Persist secret values in the secret store; return their names.

    Scoped by team so one tenant's job can never read another's, matching how
    git credentials are held.
    """
    import json

    if not secrets:
        get_secret_store().delete(str(team_id), _key(deployment_id))
        return []
    get_secret_store().set(str(team_id), _key(deployment_id), json.dumps(secrets))
    return sorted(secrets)


def load_secrets(team_id, deployment_id) -> dict[str, str]:
    import json

    raw = get_secret_store().get(str(team_id), _key(deployment_id))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def delete_secrets(team_id, deployment_id) -> None:
    get_secret_store().delete(str(team_id), _key(deployment_id))
