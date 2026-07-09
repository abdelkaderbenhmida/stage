from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Users Service", version="1.0.0")
REQUEST_COUNT = Counter("users_requests_total", "Total requests")

@app.get("/")
def root():
    REQUEST_COUNT.inc()
    return {"service": "users", "version": "1.0.0"}

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