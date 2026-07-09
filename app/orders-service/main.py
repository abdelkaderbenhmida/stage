from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Orders Service", version="1.0.0")
REQUEST_COUNT = Counter("orders_requests_total", "Total requests")

@app.get("/")
def root():
    REQUEST_COUNT.inc()
    return {"service": "orders", "version": "1.0.0"}

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