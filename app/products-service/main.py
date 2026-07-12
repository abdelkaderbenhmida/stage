import os
import secrets

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import Counter, generate_latest

from shared.log_config import setup_logging
from shared.vault_client import get_secret, SecretUnavailable, vault_health

_LOG = setup_logging("products-service")

app = FastAPI(title="Products Service", version="1.0.0")
REQUEST_COUNT = Counter("products_requests_total", "Total requests")


def _load_secrets() -> None:
    global DATABASE_URL, API_KEY
    is_dev = os.environ.get("ENVIRONMENT", "production").lower() in ("dev", "development", "local")
    DATABASE_URL = get_secret("DATABASE_URL", default=os.environ.get("DATABASE_URL"))
    API_KEY = get_secret("API_KEY", default=os.environ.get("API_KEY"))
    if not DATABASE_URL:
        if is_dev:
            DATABASE_URL = "sqlite:///./products.db"
            _LOG.warning("secret.default_used", extra={"event": "secret.default_used", "secret_name": "DATABASE_URL"})
        else:
            raise SystemExit("DATABASE_URL missing in Vault; refusing to start in production.")
    if not API_KEY:
        if is_dev:
            API_KEY = secrets.token_hex(16)
            _LOG.warning("secret.dev_ephemeral", extra={"event": "secret.dev_ephemeral", "secret_name": "API_KEY", "reason": "generated_per_process"})
        else:
            raise SystemExit("API_KEY missing in Vault; refusing to start.")


try:
    _load_secrets()
except SecretUnavailable as exc:
    _LOG.error("startup.secret_unavailable", extra={"event": "startup.secret_unavailable", "error": str(exc)})
    raise SystemExit(str(exc)) from exc

SERVICE_NAME = os.environ.get("SERVICE_NAME", "products-service")
VAULT_CONFIGURED = bool(os.environ.get("VAULT_ADDR"))


@app.get("/")
def root():
    REQUEST_COUNT.inc()
    return {
        "service": "products",
        "version": "1.0.0",
        "vault_configured": VAULT_CONFIGURED,
    }


@app.get("/livez")
def livez():
    REQUEST_COUNT.inc()
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    REQUEST_COUNT.inc()
    health = vault_health()
    return JSONResponse(
        status_code=200 if health.get("reachable") else 503,
        content={"service": "products", "vault": health},
    )


@app.get("/health")
def health():
    return readyz()


@app.get("/products")
def list_products():
    REQUEST_COUNT.inc()
    return [{"id": 1, "name": "Laptop", "price": 999.99}, {"id": 2, "name": "Mouse", "price": 29.99}]


@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type="text/plain")
