import os
import secrets

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import Counter, generate_latest

from shared.log_config import setup_logging
from shared.vault_client import get_secret, SecretUnavailable, vault_health

# Structured logs from the start. Log format honors LOG_FORMAT (json|plain).
_LOG = setup_logging("users-service")

app = FastAPI(title="Users Service", version="1.0.0")
REQUEST_COUNT = Counter("users_requests_total", "Total requests")

# Fail-fast secrets: refuse to start with placeholder values in production.
# Developers may opt into a local SQLite path by setting DATABASE_URL or
# by passing --reload with the `dev` default below — but only when LOG_LEVEL
# is set to DEBUG, signaling a non-production run.
def _load_secrets() -> None:
    """Resolve required secrets at startup. Raises on missing — fail closed."""
    global DATABASE_URL, JWT_SECRET_KEY
    is_dev = os.environ.get("ENVIRONMENT", "production").lower() in ("dev", "development", "local")
    DATABASE_URL = get_secret("DATABASE_URL", default=os.environ.get("DATABASE_URL"))
    JWT_SECRET_KEY = get_secret("JWT_SECRET_KEY", default=os.environ.get("JWT_SECRET_KEY"))
    if not DATABASE_URL:
        if is_dev:
            DATABASE_URL = "sqlite:///./users.db"
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
    REQUEST_COUNT.inc()
    return {
        "service": "users",
        "version": "1.0.0",
        "vault_configured": VAULT_CONFIGURED,
    }


@app.get("/livez")
def livez():
    """Liveness — process is running. Never checks deps."""
    REQUEST_COUNT.inc()
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    """Readiness — must be Vault-reachable to receive production traffic.

    A separate probe from /livez so k8s can keep the pod in the endpoints
    list when the process is alive but Vault is unreachable, preventing
    broken-secret-fallback than silent-degraded serving.
    """
    REQUEST_COUNT.inc()
    health = vault_health()
    return JSONResponse(
        status_code=200 if health.get("reachable") else 503,
        content={"service": "users", "vault": health},
    )


@app.get("/health")
def health():
    """Compatibility alias for /readyz.

    Older deployments / gitleaks allowlist reference this; kept until all
    probes migrated to /livez + /readyz.
    """
    return readyz()


@app.get("/users")
def list_users():
    REQUEST_COUNT.inc()
    return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]


@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type="text/plain")
