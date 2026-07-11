import os

from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from fastapi.responses import PlainTextResponse

from vault_client import get_secret

app = FastAPI(title="Orders Service", version="1.0.0")
REQUEST_COUNT = Counter("orders_requests_total", "Total requests")

DATABASE_URL = get_secret("DATABASE_URL", "sqlite:///./orders.db")
PAYMENT_GATEWAY_KEY = get_secret("PAYMENT_GATEWAY_KEY", "fallback-payment-key")

SERVICE_NAME = os.environ.get("SERVICE_NAME", "orders-service")
VAULT_ADDR = os.environ.get("VAULT_ADDR")
VAULT_CONFIGURED = bool(VAULT_ADDR)

@app.get("/")
def root():
    REQUEST_COUNT.inc()
    return {
        "service": "orders",
        "version": "1.0.0",
        "vault_configured": VAULT_CONFIGURED,
    }

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/orders")
def list_orders():
    REQUEST_COUNT.inc()
    return [{"id": 1, "user_id": 1, "product_id": 2}, {"id": 2, "user_id": 2, "product_id": 1}]

@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type="text/plain")