import os
import secrets

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from shared.log_config import setup_logging
from shared.vault_client import get_secret, SecretUnavailable, vault_health

_LOG = setup_logging("zz-shiptest")

app = FastAPI(title="zz-shiptest", version="1.0.0")

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_respect_env_var=False,
    excluded_handlers=["/livez", "/readyz", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def _load_secrets() -> None:
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
        else:
            raise SystemExit("DATABASE_URL is missing in Vault; refusing to start.")
    if not JWT_SECRET_KEY:
        if is_dev:
            JWT_SECRET_KEY = secrets.token_hex(32)
        else:
            raise SystemExit("JWT_SECRET_KEY missing in Vault; refusing to start.")


try:
    _load_secrets()
except SecretUnavailable as exc:
    _LOG.error("startup.secret_unavailable", extra={"event": "startup.secret_unavailable", "error": str(exc)})
    raise SystemExit(str(exc)) from exc

SERVICE_NAME = os.environ.get("SERVICE_NAME", "zz-shiptest")
VAULT_CONFIGURED = bool(os.environ.get("VAULT_ADDR"))


@app.get("/")
def root():
    return {"service": "zz-shiptest", "version": "1.0.0", "vault_configured": VAULT_CONFIGURED}


@app.get("/livez")
def livez():
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    health = vault_health()
    return JSONResponse(
        status_code=200 if health.get("reachable") else 503,
        content={"service": "zz-shiptest", "vault": health},
    )
