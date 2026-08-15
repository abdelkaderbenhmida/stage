import os
import secrets

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from shared.log_config import setup_logging
from shared.vault_client import get_secret, SecretUnavailable, vault_health

_LOG = setup_logging("inventory-service")

app = FastAPI(title="Inventory Service", version="1.0.0")

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_respect_env_var=False,
    excluded_handlers=["/livez", "/readyz", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def _load_secrets() -> None:
    global DATABASE_URL, STOCK_SYNC_KEY
    is_dev = os.environ.get("ENVIRONMENT", "production").lower() in ("dev", "development", "local")
    try:
        DATABASE_URL = get_secret("DATABASE_URL")
    except SecretUnavailable:
        DATABASE_URL = None
    try:
        STOCK_SYNC_KEY = get_secret("STOCK_SYNC_KEY")
    except SecretUnavailable:
        STOCK_SYNC_KEY = None
    if not DATABASE_URL:
        if is_dev:
            DATABASE_URL = "sqlite:///file::memory:?cache=shared&uri=true"
            _LOG.warning("secret.default_used", extra={"event": "secret.default_used", "secret_name": "DATABASE_URL"})
        else:
            raise SystemExit("DATABASE_URL missing in Vault; refusing to start in production.")
    if not STOCK_SYNC_KEY:
        if is_dev:
            STOCK_SYNC_KEY = secrets.token_hex(16)
            _LOG.warning("secret.dev_ephemeral", extra={"event": "secret.dev_ephemeral", "secret_name": "STOCK_SYNC_KEY", "reason": "generated_per_process"})
        else:
            raise SystemExit("STOCK_SYNC_KEY missing in Vault; refusing to start.")


try:
    _load_secrets()
except SecretUnavailable as exc:
    _LOG.error("startup.secret_unavailable", extra={"event": "startup.secret_unavailable", "error": str(exc)})
    raise SystemExit(str(exc)) from exc

SERVICE_NAME = os.environ.get("SERVICE_NAME", "inventory-service")
VAULT_CONFIGURED = bool(os.environ.get("VAULT_ADDR"))


@app.get("/")
def root():
    return {
        "service": "inventory",
        "version": "1.0.0",
        "vault_configured": VAULT_CONFIGURED,
    }


@app.get("/livez")
def livez():
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    health = vault_health()
    return JSONResponse(
        status_code=200 if health.get("reachable") else 503,
        content={"service": "inventory", "vault": health},
    )


@app.get("/inventory")
def list_inventory():
    return [{"product_id": 1, "stock": 42}, {"product_id": 2, "stock": 7}]
