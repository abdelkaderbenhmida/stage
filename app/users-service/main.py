import os

from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from fastapi.responses import PlainTextResponse

from vault_client import get_secret

app = FastAPI(title="Users Service", version="1.0.0")
REQUEST_COUNT = Counter("users_requests_total", "Total requests")

DATABASE_URL = get_secret("DATABASE_URL", "sqlite:///./users.db")
JWT_SECRET_KEY = get_secret("JWT_SECRET_KEY", "fallback-dev-key")

SERVICE_NAME = os.environ.get("SERVICE_NAME", "users-service")
VAULT_ADDR = os.environ.get("VAULT_ADDR")
VAULT_CONFIGURED = bool(VAULT_ADDR)

@app.get("/")
def root():
    REQUEST_COUNT.inc()
    return {
        "service": "users",
        "version": "1.0.0",
        "vault_configured": VAULT_CONFIGURED,
    }

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/users")
def list_users():
    REQUEST_COUNT.inc()
    return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type="text/plain")