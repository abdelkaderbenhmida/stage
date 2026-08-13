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


# ─── Live status + actions ───

class RunRef(BaseModel):
    run_id: str


class WorkflowRef(BaseModel):
    workflow: str = "ci-cd.yml"
    ref: str = ""


@app.get("/api/live/ci")
def api_live_ci() -> dict:
    return introspect.ci_runs()


@app.post("/api/live/ci/trigger")
def api_live_ci_trigger(body: WorkflowRef) -> dict:
    return _guard(introspect.ci_trigger, body.workflow, body.ref or None)


@app.post("/api/live/ci/rerun")
def api_live_ci_rerun(body: RunRef) -> dict:
    return _guard(introspect.ci_rerun, body.run_id)


@app.post("/api/live/ci/cancel")
def api_live_ci_cancel(body: RunRef) -> dict:
    return _guard(introspect.ci_cancel, body.run_id)


@app.get("/api/live/cluster")
def api_live_cluster() -> dict:
    return introspect.cluster_status()


@app.get("/api/live/pods")
def api_live_pods(namespace: str = "") -> dict:
    return introspect.pods_status(namespace or None)


@app.get("/api/live/argocd")
def api_live_argocd() -> dict:
    return introspect.argocd_apps()


@app.get("/api/live/argocd/{app_name}/resources")
def api_live_argocd_resources(app_name: str) -> dict:
    return introspect.argocd_app_resources(app_name)


class AppRef(BaseModel):
    name: str


@app.post("/api/live/argocd/sync")
def api_live_argocd_sync(body: AppRef) -> dict:
    return _guard(introspect.argocd_sync, body.name)


@app.post("/api/live/argocd/refresh")
def api_live_argocd_refresh(body: AppRef) -> dict:
    return _guard(introspect.argocd_refresh, body.name)


@app.get("/api/live/argocd/admin-password")
def api_live_argocd_admin_password() -> dict:
    return introspect.argocd_admin_password()


@app.get("/api/live/vault")
def api_live_vault() -> dict:
    return introspect.vault_status()


@app.get("/api/live/vault/secrets")
def api_live_vault_secrets(path: str = "devops-platform") -> dict:
    return introspect.vault_secrets(path)


@app.get("/api/live/vault/secrets/{service}")
def api_live_vault_secret_meta(service: str) -> dict:
    return introspect.vault_secret_metadata(service)


@app.get("/api/live/alerts")
def api_live_alerts() -> dict:
    return introspect.alerts_firing()


@app.get("/api/live/alerts/history")
def api_live_alerts_history(limit: int = 100) -> dict:
    return introspect.alert_history(limit)


# ─── Dashboards (on-demand port-forward) ───

@app.post("/api/live/dashboard/{tool}/open")
def api_dashboard_open(tool: str) -> dict:
    return _guard(introspect.open_dashboard, tool)


@app.post("/api/live/dashboard/{tool}/close")
def api_dashboard_close(tool: str) -> dict:
    return _guard(introspect.close_dashboard, tool)


@app.get("/api/live/dashboard/status")
def api_dashboard_status() -> dict:
    return introspect.dashboard_status()


# ─── Pod operations ───

class PodRef(BaseModel):
    namespace: str
    pod: str


@app.get("/api/live/pods/{namespace}/{pod}/logs")
def api_pod_logs(namespace: str, pod: str, tail: int = 200) -> dict:
    return introspect.pod_logs(namespace, pod, tail)


@app.post("/api/live/pods/restart")
def api_pod_restart(body: PodRef) -> dict:
    return _guard(introspect.pod_restart, body.namespace, body.pod)


@app.get("/api/live/pods/find")
def api_pod_find(namespace: str, prefix: str) -> dict:
    name = introspect.find_pod(namespace, prefix)
    return {"pod": name}


@app.get("/api/live/pods/{namespace}/{pod}/detail")
def api_pod_detail(namespace: str, pod: str) -> dict:
    return introspect.pod_detail(namespace, pod)


@app.get("/api/live/pods/{namespace}/{pod}/events")
def api_pod_events(namespace: str, pod: str, limit: int = 30) -> dict:
    return introspect.pod_events(namespace, pod, limit)


@app.get("/api/live/metrics/pods")
def api_pod_metrics(namespace: str) -> dict:
    return introspect.pod_metrics(namespace)


# ─── Service drill-down (aggregates pods + events + metrics + rollout) ───

@app.get("/api/live/services/{service}/drilldown")
def api_service_drilldown(service: str, namespace: str = "devops-platform") -> dict:
    return introspect.service_drilldown(service, namespace)


# ─── Rollout history / rollback ───

class RolloutRef(BaseModel):
    namespace: str
    deployment: str
    to_revision: int | None = None


@app.get("/api/live/rollout/{namespace}/{deployment}/history")
def api_rollout_history(namespace: str, deployment: str) -> dict:
    return introspect.rollout_history(namespace, deployment)


@app.post("/api/live/rollout/undo")
def api_rollout_undo(body: RolloutRef) -> dict:
    return _guard(introspect.rollout_undo, body.namespace, body.deployment, body.to_revision)


# ─── CI run logs ───

@app.get("/api/live/ci/{run_id}/logs")
def api_ci_run_logs(run_id: str) -> dict:
    return introspect.ci_run_logs(run_id)


# ─── Ops script runner ───

class ScriptRun(BaseModel):
    script: str


@app.get("/api/live/scripts")
def api_scripts_list() -> dict:
    return introspect.list_scripts()


@app.post("/api/live/scripts/run")
def api_scripts_run(body: ScriptRun) -> dict:
    return _guard(introspect.run_script, body.script)


@app.get("/api/live/scripts/{script}/output")
def api_scripts_output(script: str, offset: int = 0) -> dict:
    return introspect.script_output(script, offset)


@app.post("/api/live/scripts/{script}/stop")
def api_scripts_stop(script: str) -> dict:
    return _guard(introspect.stop_script, script)


def _guard(fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    try:
        return fn(*args)
    except introspect.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc