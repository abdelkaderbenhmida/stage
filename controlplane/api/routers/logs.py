"""Per-project logs view backed by Loki (docs/TODO.md §4.2).

The UI needs "the last N lines from this project's pods" without opening
Grafana. This endpoint proxies the central Loki query API but never
passes raw LokiQL through from the client: the query is built server-side
from the project name, so a caller can only ever see their own project's
namespace — no `{namespace=~".*"}` escalation is possible.
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from controlplane.api.deps import audit, get_current_user, get_db, get_scope
from controlplane.core.config import settings
from controlplane.core.validation import k8s_namespace
from controlplane.models import User
from controlplane.repositories.base import NotFoundError, Scope
from controlplane.repositories.projects import ProjectRepository

router = APIRouter(tags=["logs"])

_LOKI_TIMEOUT = 5.0
_DEFAULT_LIMIT = 200
_MAX_LIMIT = 2000


def _escape_logql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_query(namespace: str, search: str) -> str:
    """Server-side LogQL, never client-supplied raw."""
    query = f'{{namespace="{namespace}"}}'
    if search:
        query += f' |= "{_escape_logql_string(search)}"'
    return query


@router.get("/logs")
def project_logs(
    request: Request,
    project: str = Query(..., min_length=3, max_length=30, pattern=r"^[a-z0-9-]+$"),
    search: str = Query("", max_length=200),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    since_hours: int = Query(1, ge=1, le=168),
    db=Depends(get_db),
    user: User = Depends(get_current_user),
    scope: Scope = Depends(get_scope),
):
    """Last ``limit`` log lines from ``project``'s namespace in Loki.

    Only projects the caller can see are accepted: an unknown or foreign
    project 404s identically to the projects API, so existence is not
    leaked through this endpoint either.
    """
    try:
        project_row = ProjectRepository(db, scope).get_by_name(project)
        if project_row is None:
            raise NotFoundError("project", project)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Project not found.") from None

    # Never the raw project name: since Phase 1 that string is unique only
    # per team, not globally, so two teams' "staging" would collide onto the
    # same LogQL selector and blend — or leak — each other's log lines.
    query = build_query(k8s_namespace(project_row.id), search)
    end = time.time()
    start = end - since_hours * 3600

    try:
        response = httpx.get(
            f"{settings.loki_url}/loki/api/v1/query_range",
            params={"query": query, "start": str(int(start)), "end": str(int(end)), "limit": limit},
            timeout=_LOKI_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError:
        # Name the backend and the way to get one. Loki is not part of the
        # default install path — k8s/monitoring/loki/ exists but only
        # scripts/local-observability.sh applies it — so this is the expected
        # state on a fresh cluster, not a transient outage, and a bare
        # "unavailable" sends the reader looking for the wrong problem.
        raise HTTPException(
            status_code=502,
            detail=(
                f"Log backend unavailable: could not reach Loki at {settings.loki_url}. "
                "Loki is not installed by the default path — "
                "scripts/local-observability.sh deploys k8s/monitoring/loki/ and holds a "
                "port-forward, or set LOKI_URL to a reachable Loki."
            ),
        ) from None

    lines = []
    for stream in payload.get("data", {}).get("result", []):
        labels = stream.get("stream", {})
        for ts, line in stream.get("values", []):
            lines.append({"timestamp": ts, "line": line, "labels": labels})

    lines.sort(key=lambda entry: entry["timestamp"])
    audit(db, user.id, "project.view_logs", request, "project", str(project_row.id), team_id=project_row.team_id)
    db.commit()
    return {"project": project, "query": query, "lines": lines[-limit:]}