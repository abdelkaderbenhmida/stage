"""Devops Platform UI — repo introspection + management console.

Run:  uvicorn ui.main:app --port 8080
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import introspect

STATIC_DIR = Path(__file__).resolve().parent / "static"
START_TIME = time.time()

app = FastAPI(title="Devops Platform UI", version="1.0.0")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class AppIn(BaseModel):
    name: str


class ServiceIn(BaseModel):
    app: str = ""
    name: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/platform")
def api_platform() -> dict:
    services = introspect.discover_services()
    return {
        "overview": introspect.platform_overview(services),
        "apps": introspect.discover_apps(),
        "services": introspect.services_detail(),
        "ci": introspect.parse_ci(),
        "vault": introspect.parse_vault(),
        "monitoring": introspect.parse_monitoring(),
        "argocd": introspect.parse_argocd(),
        "helm": introspect.helm_render(services),
        "uptime_s": int(time.time() - START_TIME),
        "server_time": time.strftime("%H:%M:%S"),
    }


@app.get("/api/overview")
def api_overview() -> dict:
    return introspect.platform_overview(introspect.discover_services())


@app.get("/api/apps")
def api_apps() -> dict:
    return {"apps": introspect.discover_apps(), "count": len(introspect.discover_apps())}


@app.post("/api/apps")
def api_create_app(body: AppIn) -> dict:
    return _guard(introspect.create_app, body.name)


@app.delete("/api/apps/{app_name}")
def api_delete_app(app_name: str) -> dict:
    return _guard(introspect.delete_app, app_name)


@app.get("/api/services")
def api_services() -> dict:
    services = introspect.services_detail()
    return {"services": services, "count": len(services)}


@app.post("/api/apps/{app_name}/services")
def api_create_service(app_name: str, body: ServiceIn) -> dict:
    return _guard(introspect.create_service, app_name or body.app, body.name)


@app.delete("/api/apps/{app_name}/services/{svc_name}")
def api_delete_service(app_name: str, svc_name: str) -> dict:
    return _guard(introspect.delete_service, app_name, svc_name)


@app.get("/api/helm")
def api_helm() -> dict:
    return introspect.helm_render(introspect.discover_services())


@app.get("/api/ci")
def api_ci() -> dict:
    return introspect.parse_ci()


@app.get("/api/vault")
def api_vault() -> dict:
    return introspect.parse_vault()


@app.get("/api/monitoring")
def api_monitoring() -> dict:
    return introspect.parse_monitoring()


@app.get("/api/argocd")
def api_argocd() -> dict:
    return introspect.parse_argocd()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "uptime_s": int(time.time() - START_TIME)}


def _guard(fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    try:
        return fn(*args)
    except introspect.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc