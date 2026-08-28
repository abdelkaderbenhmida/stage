import os
import secrets

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from shared.log_config import setup_logging
from shared.vault_client import get_secret, SecretUnavailable, vault_health

# Structured logs from the start. Log format honors LOG_FORMAT (json|plain).
_LOG = setup_logging("users-service")

app = FastAPI(title="Users Service", version="1.0.0")

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_respect_env_var=False,
    excluded_handlers=["/livez", "/readyz", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# Fail-fast secrets: refuse to start with placeholder values in production.
# Dev overrides: uses aciermemory path (compatible with readOnlyRootFilesystem) or
# ephemeral token generated per-process.
def _load_secrets() -> None:
    """Resolve required secrets at startup. Raises on missing — fail closed."""
    global DATABASE_URL, JWT_SECRET_KEY
    is_dev = os.environ.get("ENVIRONMENT", "production").lower() in ("dev", "development", "local")
    try:
        DATABASE_URL = get_secret("DATABASE_URL")
    except SecretUnavailable:
        DATABASE_URL = None
    try:
        JWT_SECRET_KEY = get_secret("JWT_SECRET_KEY")
    except SecretUnavailable:
        JWT_SECRET_KEY = None
    if not DATABASE_URL:
        if is_dev:
            DATABASE_URL = "sqlite:///file::memory:?cache=shared&uri=true"
            _LOG.warning("secret.default_used", extra={"event": "secret.default_used", "secret_name": "DATABASE_URL"})
        else:
            raise SystemExit(
                "DATABASE_URL is missing in Vault and no env override is set; "
                "refusing to start in production without it."
            )
    if not JWT_SECRET_KEY:
        if is_dev:
            JWT_SECRET_KEY = secrets.token_hex(32)
            _LOG.warning("secret.dev_ephemeral", extra={"event": "secret.dev_ephemeral", "secret_name": "JWT_SECRET_KEY", "reason": "generated_per_process"})
        else:
            raise SystemExit("JWT_SECRET_KEY missing in Vault; refusing to start.")

try:
    _load_secrets()
except SecretUnavailable as exc:
    # Surface a clear startup failure instead of running with fake secrets.
    _LOG.error("startup.secret_unavailable", extra={"event": "startup.secret_unavailable", "error": str(exc)})
    raise SystemExit(str(exc)) from exc

SERVICE_NAME = os.environ.get("SERVICE_NAME", "users-service")
VAULT_CONFIGURED = bool(os.environ.get("VAULT_ADDR"))


@app.get("/")
def root():
    return {
        "service": "users",
        "version": "1.0.0",
        "vault_configured": VAULT_CONFIGURED,
    }


@app.get("/livez")
def livez():
    """Liveness — process is running. Never checks deps."""
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    """Readiness — must be Vault-reachable to receive production traffic.

    A separate probe from /livez so k8s can keep the pod in the endpoints
    list when the process is alive but Vault is unreachable, preventing
    broken-secret-fallback than silent-degraded serving.
    """
    health = vault_health()
    return JSONResponse(
        status_code=200 if health.get("reachable") else 503,
        content={"service": "users", "vault": health},
    )


@app.get("/users")
def list_users():
    return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
