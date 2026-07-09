from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Products Service", version="1.0.0")
REQUEST_COUNT = Counter("products_requests_total", "Total requests")

@app.get("/")
def root():
    REQUEST_COUNT.inc()
    return {"service": "products", "version": "1.0.0"}

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