"""Per-project metrics view backed by Prometheus (multi-tenancy plan §4.1).

The counterpart to ``routers/logs.py``. The UI needs "how much CPU/memory is
this environment using, and are its pods healthy" without handing anyone a
Grafana login.

Same hard rule as the logs endpoint: **no client-supplied PromQL ever reaches
Prometheus.** Every query is assembled server-side around the caller's own
namespace, derived from the project's UUID (``core.validation.k8s_namespace``),
so a caller cannot widen the selector to another tenant's namespace — the
Phase 5 note on ``k8s/monitoring/prometheus/prometheus.yaml`` says any
tenant-facing metrics view must enforce exactly this, because the shared TSDB
has no per-tenant access control of its own.
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from controlplane.api.deps import get_db, get_scope
from controlplane.core.config import settings
from controlplane.core.validation import k8s_namespace
from controlplane.repositories.base import NotFoundError, Scope
from controlplane.repositories.projects import ProjectRepository

router = APIRouter(tags=["monitoring"])

_PROM_TIMEOUT = 5.0

# Fixed catalogue of queries. A client picks a key, never a query — the only
# thing it can influence is which of these run and over what window.
#
# ``{ns}`` is substituted with the project's own namespace. Nothing else in
# these strings is templated, so there is no way to inject a second selector.
_PANELS: dict[str, dict[str, str]] = {
    "cpu": {
        "title": "CPU cores",
        "unit": "cores",
        "query": 'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}",container!=""}}[5m]))',
    },
    "memory": {
        "title": "Memory",
        "unit": "bytes",
        "query": 'sum(container_memory_working_set_bytes{{namespace="{ns}",container!=""}})',
    },
    # kube-state-metrics carries the *subject* pod's namespace. Whether that
    # arrives as `namespace` or `exported_namespace` depends on the scrape
    # config: with honor_labels off (common), the scraping target's own
    # namespace wins and the real one is preserved as `exported_namespace`.
    # `a or b` yields a when it has samples and b otherwise, so this matches
    # either convention without double counting.
    "pods": {
        "title": "Running pods",
        "unit": "pods",
        "query": (
            'count(kube_pod_status_phase{{namespace="{ns}",phase="Running"}} == 1)'
            ' or count(kube_pod_status_phase{{exported_namespace="{ns}",phase="Running"}} == 1)'
            " or vector(0)"
        ),
    },
    "restarts": {
        "title": "Container restarts",
        "unit": "restarts",
        "query": (
            'sum(kube_pod_container_status_restarts_total{{namespace="{ns}"}})'
            ' or sum(kube_pod_container_status_restarts_total{{exported_namespace="{ns}"}})'
            " or vector(0)"
        ),
    },
    # cadvisor scrapes via the kubelet, which carries the pod's real
    # namespace directly — no honor_labels split like the kube-state-metrics
    # panels above.
    "network_rx": {
        "title": "Network in",
        "unit": "bytes/s",
        "query": 'sum(rate(container_network_receive_bytes_total{{namespace="{ns}"}}[5m]))',
    },
    "network_tx": {
        "title": "Network out",
        "unit": "bytes/s",
        "query": 'sum(rate(container_network_transmit_bytes_total{{namespace="{ns}"}}[5m]))',
    },
}


def _require_project(db, scope: Scope, project_id):
    try:
        project = ProjectRepository(db, scope).get_project(project_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.") from None
    return project


@router.get("/projects/{project_id}/metrics")
def project_metrics(
    project_id: str,
    since_minutes: int = Query(60, ge=5, le=1440),
    step_seconds: int = Query(60, ge=15, le=3600),
    db=Depends(get_db),
    scope: Scope = Depends(get_scope),
):
    """Time series for this project's namespace, one entry per panel.

    A project the caller cannot see 404s exactly like the projects API, so
    this endpoint never confirms that someone else's project exists.
    """
    import uuid as _uuid

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid project id.") from None

    project = _require_project(db, scope, pid)
    namespace = k8s_namespace(project.id)

    end = time.time()
    start = end - since_minutes * 60

    panels = []
    unavailable = False
    for key, panel in _PANELS.items():
        query = panel["query"].format(ns=namespace)
        series: list[dict] = []
        try:
            response = httpx.get(
                f"{settings.prometheus_url}/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(int(start)),
                    "end": str(int(end)),
                    "step": str(step_seconds),
                },
                timeout=_PROM_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            for result in payload.get("data", {}).get("result", []):
                for ts, value in result.get("values", []):
                    try:
                        series.append({"t": float(ts), "v": float(value)})
                    except (TypeError, ValueError):
                        continue
        except httpx.HTTPError:
            # One shared backend for every panel: if it is down, say so once
            # rather than failing the whole page — the rest of the project
            # view is still useful without metrics.
            unavailable = True

        panels.append(
            {
                "key": key,
                "title": panel["title"],
                "unit": panel["unit"],
                "latest": series[-1]["v"] if series else None,
                "series": series,
            }
        )

    return {
        "project": project.name,
        "namespace": namespace,
        "window_minutes": since_minutes,
        "backend_available": not unavailable,
        "panels": panels,
    }
