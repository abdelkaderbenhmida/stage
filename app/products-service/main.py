import os

from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from fastapi.responses import PlainTextResponse

from vault_client import get_secret

app = FastAPI(title="Products Service", version="1.0.0")
REQUEST_COUNT = Counter("products_requests_total", "Total requests")

DATABASE_URL = get_secret("DATABASE_URL", "sqlite:///./products.db")
API_KEY = get_secret("API_KEY", "fallback-api-key")

SERVICE_NAME = os.environ.get("SERVICE_NAME", "products-service")
VAULT_ADDR = os.environ.get("VAULT_ADDR")
VAULT_CONFIGURED = bool(VAULT_ADDR)

@app.get("/")
def root():
    REQUEST_COUNT.inc()
    return {
        "service": "products",
        "version": "1.0.0",
        "vault_configured": VAULT_CONFIGURED,
    }

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/products")
def list_products():
    REQUEST_COUNT.inc()
    return [{"id": 1, "name": "Laptop", "price": 999.99}, {"id": 2, "name": "Mouse", "price": 29.99}]

@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type="text/plain")