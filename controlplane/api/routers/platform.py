"""Platform ops console — repo introspection + live cluster/CI/vault control.

Ported from ``ui/`` (a standalone, unauthenticated FastAPI app) so the same
introspection and live-ops actions are reachable from the control plane's
UI, behind its existing JWT auth. The underlying logic in
``controlplane.platform_ops`` is unchanged from ``ui/introspect.py``; only
the HTTP wiring (auth, routing) differs.

Every route here acts on the control-plane host itself (repo checkouts, the
shared cluster's kubectl/terraform, ArgoCD, Vault) — there is no per-tenant
data to scope, so this is gated on platform-admin, not team membership
(multi-tenancy plan Phase 0).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from controlplane import platform_ops
from controlplane.api.rbac import require_platform_admin

router = APIRouter(prefix="/platform", tags=["platform"], dependencies=[Depends(require_platform_admin)])

START_TIME = time.time()


def _guard(fn: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    try:
        return fn(*args)
    except platform_ops.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AppIn(BaseModel):
    name: str


class ServiceIn(BaseModel):
    app: str = ""
    name: str


class ShipIn(BaseModel):
    app: str = ""
    name: str
    open_pr: bool = True


class RunRef(BaseModel):
    run_id: str


class WorkflowRef(BaseModel):
    workflow: str = "ci-cd.yml"
    ref: str = ""


class AppRef(BaseModel):
    name: str


class PodRef(BaseModel):
    namespace: str
    pod: str


class RolloutRef(BaseModel):
    namespace: str
    deployment: str
    to_revision: int | None = None


class ScriptRun(BaseModel):
    script: str


@router.get("")
def api_platform() -> dict:
    services = platform_ops.discover_services()
    return {
        "overview": platform_ops.platform_overview(services),
        "apps": platform_ops.discover_apps(),
        "services": platform_ops.services_detail(),
        "ci": platform_ops.parse_ci(),
        "vault": platform_ops.parse_vault(),
        "monitoring": platform_ops.parse_monitoring(),
        "argocd": platform_ops.parse_argocd(),
        "helm": platform_ops.helm_render(services),
        "uptime_s": int(time.time() - START_TIME),
        "server_time": time.strftime("%H:%M:%S"),
    }


@router.get("/overview")
def api_overview() -> dict:
    return platform_ops.platform_overview(platform_ops.discover_services())


@router.get("/apps")
def api_apps() -> dict:
    return {"apps": platform_ops.discover_apps(), "count": len(platform_ops.discover_apps())}


@router.post("/apps")
def api_create_app(body: AppIn) -> dict:
    return _guard(platform_ops.create_app, body.name)


@router.delete("/apps/{app_name}")
def api_delete_app(app_name: str) -> dict:
    return _guard(platform_ops.delete_app, app_name)


@router.get("/services")
def api_services() -> dict:
    services = platform_ops.services_detail()
    return {"services": services, "count": len(services)}


@router.post("/apps/{app_name}/services")
def api_create_service(app_name: str, body: ServiceIn) -> dict:
    return _guard(platform_ops.create_service, app_name or body.app, body.name)


@router.delete("/apps/{app_name}/services/{svc_name}")
def api_delete_service(app_name: str, svc_name: str) -> dict:
    return _guard(platform_ops.delete_service, app_name, svc_name)


# ─── Ship flow (WS-A): create → branch → push → PR → stage tracker ───


@router.post("/ship/service")
def api_ship_service(body: ShipIn) -> dict:
    return _guard(platform_ops.ship_service, body.app, body.name, body.open_pr)


@router.post("/ship/{service}/secrets")
def api_ship_secrets(service: str) -> dict:
    return _guard(platform_ops.seed_service_secrets, service)


@router.post("/ship/vault/resync")
def api_ship_vault_resync() -> dict:
    return _guard(platform_ops.sync_service_list)


@router.post("/ship/vault/setup")
def api_ship_vault_setup() -> dict:
    return _guard(platform_ops.rerun_vault_setup)


@router.get("/ship/{service}/pipeline")
def api_ship_pipeline(service: str) -> dict:
    return platform_ops.service_pipeline(service)


# ─── Infrastructure control (WS-B) ───


@router.get("/infra/capacity")
def api_infra_capacity() -> dict:
    return platform_ops.cluster_capacity()


@router.get("/infra/terraform")
def api_infra_terraform() -> dict:
    return platform_ops.terraform_drift()


@router.post("/infra/terraform/reconcile")
def api_infra_terraform_reconcile() -> dict:
    return _guard(platform_ops.terraform_reconcile)


@router.get("/infra/preflight")
def api_infra_preflight(disk_gb: int = 0, mem_mb: int = 0) -> dict:
    return platform_ops.node_preflight(disk_gb or None, mem_mb or None)


@router.get("/helm")
def api_helm() -> dict:
    return platform_ops.helm_render(platform_ops.discover_services())


@router.get("/ci")
def api_ci() -> dict:
    return platform_ops.parse_ci()


@router.get("/vault")
def api_vault() -> dict:
    return platform_ops.parse_vault()


@router.get("/monitoring")
def api_monitoring() -> dict:
    return platform_ops.parse_monitoring()


@router.get("/argocd")
def api_argocd() -> dict:
    return platform_ops.parse_argocd()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "uptime_s": int(time.time() - START_TIME)}


# ─── Live status + actions ───


@router.get("/live/ci")
def api_live_ci() -> dict:
    return platform_ops.ci_runs()


@router.get("/ci/runs/{run_id}/graph")
def api_ci_run_graph(run_id: str) -> dict:
    """Return a pipeline graph for a GitHub Actions run.

    Edges from workflow file (parse_ci), statuses from gh run view.
    Degraded path: when gh unreachable, returns reachable:false + nodes/edges with all skipped.
    """
    return platform_ops.ci_run_graph(run_id)


@router.post("/live/ci/trigger")
def api_live_ci_trigger(body: WorkflowRef) -> dict:
    return _guard(platform_ops.ci_trigger, body.workflow, body.ref or None)


@router.post("/live/ci/rerun")
def api_live_ci_rerun(body: RunRef) -> dict:
    return _guard(platform_ops.ci_rerun, body.run_id)


@router.post("/live/ci/cancel")
def api_live_ci_cancel(body: RunRef) -> dict:
    return _guard(platform_ops.ci_cancel, body.run_id)


@router.get("/live/cluster")
def api_live_cluster() -> dict:
    return platform_ops.cluster_status()


@router.get("/live/pods")
def api_live_pods(namespace: str = "") -> dict:
    return platform_ops.pods_status(namespace or None)


@router.get("/live/argocd")
def api_live_argocd() -> dict:
    return platform_ops.argocd_apps()


@router.get("/live/argocd/{app_name}/resources")
def api_live_argocd_resources(app_name: str) -> dict:
    return platform_ops.argocd_app_resources(app_name)


@router.post("/live/argocd/sync")
def api_live_argocd_sync(body: AppRef) -> dict:
    return _guard(platform_ops.argocd_sync, body.name)


@router.post("/live/argocd/refresh")
def api_live_argocd_refresh(body: AppRef) -> dict:
    return _guard(platform_ops.argocd_refresh, body.name)


@router.get("/live/argocd/admin-password")
def api_live_argocd_admin_password() -> dict:
    return platform_ops.argocd_admin_password()


@router.get("/live/vault")
def api_live_vault() -> dict:
    return platform_ops.vault_status()


@router.get("/live/vault/secrets")
def api_live_vault_secrets(path: str = "devops-platform") -> dict:
    return platform_ops.vault_secrets(path)


@router.get("/live/vault/secrets/{service}")
def api_live_vault_secret_meta(service: str) -> dict:
    return platform_ops.vault_secret_metadata(service)


@router.get("/live/alerts")
def api_live_alerts() -> dict:
    return platform_ops.alerts_firing()


@router.get("/live/alerts/history")
def api_live_alerts_history(limit: int = 100) -> dict:
    return platform_ops.alert_history(limit)


@router.get("/live/drift")
def api_live_drift(namespaces: str = "") -> dict:
    ns_list = [n.strip() for n in namespaces.split(",") if n.strip()] or None
    return platform_ops.drift_report(ns_list)


# ─── Logs ───


@router.get("/live/logs/status")
def api_logs_status() -> dict:
    return platform_ops.es_status()


@router.get("/live/logs/pipeline")
def api_logs_pipeline() -> dict:
    return platform_ops.log_pipeline_health()


@router.get("/live/logs/search")
def api_logs_search(service: str = "", q: str = "", limit: int = 100, since: str = "now-1h") -> dict:
    return platform_ops.es_search_logs(service, q, limit, since)


# ─── Dashboards (on-demand port-forward) ───


@router.post("/live/dashboard/{tool}/open")
def api_dashboard_open(tool: str) -> dict:
    return _guard(platform_ops.open_dashboard, tool)


@router.post("/live/dashboard/{tool}/close")
def api_dashboard_close(tool: str) -> dict:
    return _guard(platform_ops.close_dashboard, tool)


@router.get("/live/dashboard/status")
def api_dashboard_status() -> dict:
    return platform_ops.dashboard_status()


# ─── Pod operations ───


@router.get("/live/pods/{namespace}/{pod}/logs")
def api_pod_logs(namespace: str, pod: str, tail: int = 200) -> dict:
    return platform_ops.pod_logs(namespace, pod, tail)


@router.post("/live/pods/restart")
def api_pod_restart(body: PodRef) -> dict:
    return _guard(platform_ops.pod_restart, body.namespace, body.pod)


@router.get("/live/pods/find")
def api_pod_find(namespace: str, prefix: str) -> dict:
    name = platform_ops.find_pod(namespace, prefix)
    return {"pod": name}


@router.get("/live/pods/{namespace}/{pod}/detail")
def api_pod_detail(namespace: str, pod: str) -> dict:
    return platform_ops.pod_detail(namespace, pod)


@router.get("/live/pods/{namespace}/{pod}/events")
def api_pod_events(namespace: str, pod: str, limit: int = 30) -> dict:
    return platform_ops.pod_events(namespace, pod, limit)


@router.get("/live/metrics/pods")
def api_pod_metrics(namespace: str) -> dict:
    return platform_ops.pod_metrics(namespace)


# ─── Service drill-down (aggregates pods + events + metrics + rollout) ───


@router.get("/live/services/{service}/drilldown")
def api_service_drilldown(service: str, namespace: str = "devops-platform") -> dict:
    return platform_ops.service_drilldown(service, namespace)


# ─── Rollout history / rollback ───


@router.get("/live/rollout/{namespace}/{deployment}/history")
def api_rollout_history(namespace: str, deployment: str) -> dict:
    return platform_ops.rollout_history(namespace, deployment)


@router.post("/live/rollout/undo")
def api_rollout_undo(body: RolloutRef) -> dict:
    return _guard(platform_ops.rollout_undo, body.namespace, body.deployment, body.to_revision)


# ─── CI run logs ───


@router.get("/live/ci/{run_id}/logs")
def api_ci_run_logs(run_id: str) -> dict:
    return platform_ops.ci_run_logs(run_id)


# ─── Ops script runner ───


@router.get("/live/scripts")
def api_scripts_list() -> dict:
    return platform_ops.list_scripts()


@router.post("/live/scripts/run")
def api_scripts_run(body: ScriptRun) -> dict:
    return _guard(platform_ops.run_script, body.script)


@router.get("/live/scripts/{script}/output")
def api_scripts_output(script: str, offset: int = 0) -> dict:
    return platform_ops.script_output(script, offset)


@router.post("/live/scripts/{script}/stop")
def api_scripts_stop(script: str) -> dict:
    return _guard(platform_ops.stop_script, script)
