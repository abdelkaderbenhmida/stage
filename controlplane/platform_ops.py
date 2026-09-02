"""Repo introspection + management for the Devops Platform UI.

Reads and (for the console actions) writes the repository on disk only.
No cluster access. Every result is computed from the actual files so the
UI is always in sync with the repo.

Service layout — two levels are supported:
  flat:   app/<service>/main.py                (legacy, "default" app)
  nested: app/<app>/<service>/main.py          (grouped apps)
The Kubernetes/registry/Vault name of a nested service is "<app>-<service>".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
SERVICE_MARKER = "main.py"
DEFAULT_APP = "default"
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
# Ceiling on concurrent service_pipeline() computations. Each one spawns
# several short-lived subprocesses, so this bounds how many git/gh/kubectl
# processes the console can have in flight at once on a large monorepo.
_PIPELINE_MAX_WORKERS = 8

# ── Personal / host-specific config — from .env, never hardcoded here.
# Loaded once at import so every module reads the same values. Missing .env
# falls back to per-call defaults (reported as unreachable in the UI), so the
# platform still boots for read-only use without local config.
def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and value and key not in os.environ:
                os.environ[key] = value
    except OSError:
        return


_load_dotenv()

SSH_USER = os.environ.get("SSH_USER", "devops")
NETWORK_CIDR = os.environ.get("NETWORK_CIDR", "")
K8S_MASTER_NAME = os.environ.get("K8S_MASTER_NAME", "master-01")
MASTER_IP = os.environ.get("MASTER_IP", "")
MASTER_SSH_TARGET = f"{SSH_USER}@{MASTER_IP}" if MASTER_IP else ""
# The platform's own namespaces. Must track k8s/apps/chart/values.yaml
# `namespace:` and k8s/monitoring's own namespace — both were hardcoded
# "devops-platform"/"monitoring" literals scattered through this file, so an
# operator who changed the Helm value silently broke every Operations-console
# query against the old name.
PLATFORM_NAMESPACE = os.environ.get("PLATFORM_NAMESPACE", "devops-platform")
MONITORING_NAMESPACE = os.environ.get("MONITORING_NAMESPACE", "monitoring")


class ServiceError(ValueError):
    pass


def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Failure must look like absence (""), never a truthy echo: on error
        # git still prints the offending argument to stdout (e.g. rev-parse
        # of a missing ref echoes "origin/service/x" before failing).
        if out.returncode != 0:
            return ""
        return out.stdout.strip()
    except Exception:
        return ""


def git_info() -> dict[str, str]:
    return {
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": _git(["rev-parse", "--short", "HEAD"]),
        "message": _git(["log", "-1", "--pretty=%s"]),
        "author": _git(["log", "-1", "--pretty=%an"]),
        "date": _git(["log", "-1", "--pretty=%ad", "--date=format:%Y-%m-%d %H:%M"]),
    }


def _iter_service_dirs() -> list[tuple[str | None, Path]]:
    """(app_name or None, service_dir) for every discovered service."""
    found = []
    if not APP_DIR.is_dir():
        return found
    for m in sorted(APP_DIR.glob("*/main.py")):
        found.append((None, m.parent))
    for m in sorted(APP_DIR.glob("*/*/main.py")):
        found.append((m.parent.parent.name, m.parent))
    return found


def _k8s_name(app: str | None, svc_dir: Path) -> str:
    base = svc_dir.name
    return f"{app}-{base}" if app else base


MARKER_NAME = "service.yaml"


def _svc_dir_from_k8s_name(name: str) -> tuple[str | None, Path]:
    """Reverse of _k8s_name: (app_name or None, svc_dir) for a k8s name.
    Flat wins — a nested app/<app>/<svc> dir with the same k8s name is
    impossible for the existing layout, but check nested first only if the
    flat dir has no main.py."""
    flat = APP_DIR / name
    if (flat / SERVICE_MARKER).is_file():
        return (None, flat)
    for m in sorted(APP_DIR.glob("*/*/main.py")):
        if _k8s_name(m.parent.parent.name, m.parent) == name:
            return (m.parent.parent.name, m.parent)
    return (None, flat)


def _list_service_markers() -> list[dict[str, Any]]:
    """Read every app/<svc>/service.yaml marker → {name, tag, path}."""
    out = []
    if not APP_DIR.is_dir():
        return out
    for m in sorted(APP_DIR.glob("*/service.yaml")):
        data = _load_yaml_docs(m)
        doc = data[0] if data else {}
        out.append({"name": doc.get("name") or m.parent.name, "tag": doc.get("tag"), "path": str(m.relative_to(ROOT))})
    for m in sorted(APP_DIR.glob("*/*/service.yaml")):
        data = _load_yaml_docs(m)
        doc = data[0] if data else {}
        out.append({"name": doc.get("name") or _k8s_name(m.parent.parent.name, m.parent), "tag": doc.get("tag"), "path": str(m.relative_to(ROOT))})
    return out


def _marker_path(app_name: str | None, svc_dir: Path) -> Path:
    return svc_dir / MARKER_NAME


def _write_service_marker(app_name: str | None, svc_dir: Path, tag: str = "secondary") -> Path:
    k8s = _k8s_name(None if app_name in (None, "", DEFAULT_APP) else app_name, svc_dir)
    marker = _marker_path(app_name, svc_dir)
    marker.write_text(
        "# ArgoCD ApplicationSet discovery marker. The `files` generator parses this\n"
        "# YAML to build one Application per service — `name` becomes the k8s/image\n"
        "# name, `tag` the image tag (CI rewrites it to commit-<sha7> after a\n"
        "# successful build; the default `secondary` is the floating branch tag).\n"
        "# main.py remains the discovery contract for CI and introspect.py.\n"
        f"name: {k8s}\n"
        f"tag: {tag}\n"
    )
    return marker


def discover_services() -> list[str]:
    return sorted({_k8s_name(a, d) for a, d in _iter_service_dirs()})


def discover_apps() -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[str | None, Path]]] = {DEFAULT_APP: []}
    with_services: set[Path] = set()
    for app, svc_dir in _iter_service_dirs():
        a = app or DEFAULT_APP
        grouped.setdefault(a, []).append((app, svc_dir))
        if app:
            with_services.add(svc_dir.parent)
    if APP_DIR.is_dir():
        for d in sorted(APP_DIR.iterdir()):
            if (
                d.is_dir()
                and d.name != "shared"
                and not (d / SERVICE_MARKER).is_file()
                and d not in with_services
            ):
                grouped.setdefault(d.name, [])
    return [
        {
            "name": app_name,
            "path": "app/" if app_name == DEFAULT_APP else f"app/{app_name}",
            "services": [s["name"] for s in _services_of(grouped[app_name])],
        }
        for app_name in sorted(grouped)
    ]


def _services_of(entries: list[tuple[str | None, Path]]) -> list[dict[str, Any]]:
    return [_service_meta(a, d) for a, d in sorted(entries, key=lambda e: e[1].name)]


def _service_meta(app: str | None, svc_dir: Path) -> dict[str, Any]:
    name = _k8s_name(app, svc_dir)
    main = svc_dir / SERVICE_MARKER
    endpoints = ["/livez", "/readyz", "/metrics"]
    has_requirements = (svc_dir / "requirements.txt").is_file()
    title = svc_dir.name
    version = "unknown"
    uses_vault = False
    try:
        text = main.read_text()
    except OSError:
        text = ""
    for m in re.finditer(r'@app\.get\("([^"]+)"\)', text):
        p = m.group(1)
        if p not in endpoints:
            endpoints.append(p)
    title_m = re.search(r'FastAPI\(title="([^"]+)"', text)
    if title_m:
        title = title_m.group(1)
    ver_m = re.search(r'FastAPI\([^)]*version="([^"]+)"', text)
    if ver_m:
        version = ver_m.group(1)
    uses_vault = "shared.vault_client" in text and "get_secret" in text
    shared_imports = sorted(
        imp[0] for imp in re.findall(r"from (shared[\w\.]*|shared\.\w+) import", text)
    )
    req_lines = 0
    req = svc_dir / "requirements.txt"
    if req.is_file():
        try:
            req_lines = len(
                [line for line in req.read_text().splitlines() if line.strip() and not line.startswith("#")]
            )
        except OSError:
            req_lines = 0
    return {
        "name": name,
        "app": app or DEFAULT_APP,
        "key": str(svc_dir.relative_to(ROOT)),
        "title": title,
        "version": version,
        "endpoints": sorted(set(endpoints)),
        "has_requirements": has_requirements,
        "uses_vault": uses_vault,
        "shared_imports": [s for s in shared_imports if s.startswith("shared")],
        "loc": len(text.splitlines()),
        "requirements_num": req_lines,
    }


def services_detail() -> list[dict[str, Any]]:
    return [_service_meta(a, d) for a, d in _iter_service_dirs()]


def _load_yaml_docs(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        docs = list(yaml.safe_load_all(path.read_text()))
    except yaml.YAMLError:
        return []
    return [d for d in docs if isinstance(d, dict)]


def load_yaml_docs(rel_path: str) -> list[dict[str, Any]]:
    return _load_yaml_docs(ROOT / rel_path)


def helm_render(services: list[str]) -> dict[str, Any]:
    default = load_yaml_docs("k8s/apps/chart/values.yaml")
    default = default[0] if default else {}
    values = {
        "services": [{"name": s} for s in services],
        **{k: v for k, v in default.items() if not k.startswith("services")},
    }
    obj = None
    try:
        doc = yaml.safe_dump(values)
        out = subprocess.run(
            [
                "helm",
                "template",
                "apps",
                str(ROOT / "k8s/apps/chart"),
                "-f",
                "-",
            ],
            input=doc,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=30,
        )
        obj = {
            "ok": out.returncode == 0,
            "error": out.stderr.strip() if out.returncode != 0 else None,
            "objects": _parse_helm_output(out.stdout),
        }
    except FileNotFoundError:
        obj = {"ok": False, "error": "helm not found on PATH", "objects": []}
    except subprocess.TimeoutExpired:
        obj = {"ok": False, "error": "helm template timed out", "objects": []}
    if obj is None:
        obj = {"ok": False, "error": "unknown failure", "objects": []}
    return _summarize_helm(obj, services)


def _parse_helm_output(out: str) -> list[dict[str, Any]]:
    objs = []
    for doc in yaml.safe_load_all(out):
        if not isinstance(doc, dict) or doc.get("kind") in ("", None):
            continue
        if doc.get("kind") == "List" and doc.get("items"):
            objs.extend(doc["items"])
            continue
        objs.append(doc)
    return [
        {
            "kind": o.get("kind"),
            "name": (o.get("metadata") or {}).get("name"),
            "namespace": (o.get("metadata") or {}).get("namespace"),
        }
        for o in objs
    ]


def _summarize_helm(
    result: dict[str, Any], services: list[str]
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    per_service: dict[str, list[str]] = {}
    shared: list[dict[str, Any]] = []
    for o in result["objects"]:
        counts[o["kind"]] = counts.get(o["kind"], 0) + 1
        if o["kind"] in ("Deployment", "Service", "ServiceAccount",
                         "HorizontalPodAutoscaler", "PodDisruptionBudget"):
            per_service.setdefault(o["name"], []).append(o["kind"])
        else:
            shared.append(o)
    return {
        **result,
        "counts": counts,
        "total": len(result["objects"]),
        "per_service": per_service,
        "shared": shared,
        "expected_per_service": sorted({"Deployment", "Service", "ServiceAccount",
                                        "HorizontalPodAutoscaler", "PodDisruptionBudget"}),
        "expected_shared": sorted({"Role", "RoleBinding", "ServiceMonitor"}),
        "formula": f"{len(services)}×5 + 3 = {len(services) * 5 + 3}",
    }


def _on_value(wf: dict[str, Any]) -> dict[str, Any]:
    for k in ("on", True, 1):
        v = wf.get(k)
        if isinstance(v, dict):
            return v
    return {}


def parse_ci() -> dict[str, Any]:
    path = ROOT / ".github/workflows/ci-cd.yml"
    docs = _load_yaml_docs(path)
    wf = docs[0] if docs else {}
    on_value = _on_value(wf)
    triggers = {
        "push": _triggers(on_value.get("push")),
        "pull_request": _triggers(on_value.get("pull_request")),
        "schedule": _triggers(on_value.get("schedule")),
        "workflow_dispatch": ["manual"] if "workflow_dispatch" in on_value else [],
    }
    jobs: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for jname, spec in (wf.get("jobs") or {}).items():
        needs = spec.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        jobs.append(
            {
                "id": jname,
                "name": spec.get("name", jname),
                "needs": needs,
                "if": spec.get("if", ""),
                "runs_on": spec.get("runs-on", ""),
                "matrix": _matrix_summary(spec),
                "steps": [(s.get("name") or s.get("uses") or s.get("run", "").split("\n")[0][:60])
                          for s in spec.get("steps", [])],
            }
        )
        edges.extend({"from": n, "to": jname} for n in needs)
    discover = next((j for j in jobs if j["id"] == "discover"), None)
    return {
        "file": str(path.relative_to(ROOT)),
        "triggers": triggers,
        "concurrency": wf.get("concurrency"),
        "permissions": wf.get("permissions"),
        "env": wf.get("env"),
        "jobs": jobs,
        "edges": edges,
        "discovery": discover,
        "uses_fromjson": [
            j["id"] for j in jobs
            if "fromJSON(" in yaml.safe_dump(_raw_jobs(wf).get(j["id"], {})) or
            j["matrix"]["from_json"]
        ],
    }


def _raw_jobs(wf: dict[str, Any]) -> dict[str, Any]:
    return wf.get("jobs") or {}


def _triggers(spec: Any) -> list[str]:
    if isinstance(spec, dict):
        return list(spec.keys())
    if isinstance(spec, list):
        out = []
        for item in spec:
            if isinstance(item, dict):
                out.append(str(item.get("cron") or str(item)[:60]))
            else:
                out.append(str(item))
        return out
    if spec is not None:
        return [str(spec)]
    return []


def _matrix_summary(spec: dict[str, Any]) -> dict[str, Any]:
    strategy = spec.get("strategy") or {}
    matrix = strategy.get("matrix") or {}
    summary: dict[str, Any] = {"has_matrix": bool(matrix), "from_json": False, "keys": []}
    if not matrix:
        return summary
    summary["keys"] = list(matrix.keys())
    for v in matrix.values():
        if isinstance(v, str) and "fromJSON(" in v:
            summary["from_json"] = True
    raw = spec.get("matrix")
    if isinstance(raw, dict):
        for value in raw.values():
            if isinstance(value, str) and "fromJSON(" in value:
                summary["from_json"] = True
    return summary


def parse_vault() -> dict[str, Any]:
    docs = load_yaml_docs("k8s/vault/manifests.yaml")
    objects = [{"kind": d.get("kind"), "name": (d.get("metadata") or {}).get("name"),
                "namespace": (d.get("metadata") or {}).get("namespace")} for d in docs]
    setup = next((d for d in docs if (d.get("metadata") or {}).get("name") == "vault-setup"), None)
    setup_script = (setup or {}).get("data", {}).get("setup.sh", "")
    loop = _extract_vault_loop(setup_script)
    job = next((d for d in docs if (d.get("metadata") or {}).get("name") == "vault-setup-job"), None)
    services_cm = None
    if job:
        for ctr in (job.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])):
            for env in ctr.get("env", []):
                vf = env.get("valueFrom", {})
                ref = vf.get("configMapKeyRef") or vf.get("secretKeyRef")
                if ref and ref.get("name") == "devops-service-list":
                    services_cm = env.get("name")
    return {
        "file": "k8s/vault/manifests.yaml",
        "objects": objects,
        "setup_script_blocks": _script_blocks(setup_script),
        "per_service": loop,
        "services_source": services_cm,
        "token_source": "vault-root-token (cross-namespace Secret ref)",
        "fail_closed": "set -e" in setup_script,
    }


def _extract_vault_loop(script: str) -> dict[str, Any]:
    policy_match = re.search(
        r'vault policy write "devops-platform-\$\{svc\}"', script
    )
    k8s_match = re.search(
        r'bound_service_account_names="\$\{svc\}-sa"', script
    )
    kv_match = re.search(r"vault kv put secret/devops-platform/\$\{svc\}", script)
    role_match = re.search(r'vault write "auth/kubernetes/role/\$\{svc\}"', script)
    return {
        "present": policy_match is not None,
        "policy_template": "devops-platform-${svc}",
        "k8s_role_template": "${svc}",
        "bound_sa_template": "${svc}-sa",
        "kv_path_template": "secret/devops-platform/${svc}",
        "policy_write": policy_match is not None,
        "k8s_role_write": role_match is not None,
        "sa_bound": k8s_match is not None,
        "kv_seed": kv_match is not None,
    }


def _script_blocks(script: str) -> list[str]:
    return [b for b in (line.strip() for line in script.splitlines() if line.strip()) if not b.startswith("#")]


def parse_monitoring() -> dict[str, Any]:
    prom_rules = load_yaml_docs("k8s/monitoring/alertmanager/rules.yaml")
    rule = next((d for d in prom_rules if d.get("kind") == "PrometheusRule"), {})
    groups = []
    slo_labels: set[str] = set()
    for g in (rule.get("spec", {}).get("groups", [])):
        for r in g.get("rules", []):
            labels = r.get("labels", {})
            if labels.get("slo") or labels.get("incident"):
                slo_labels.add(labels.get("slo") or f"incident-{labels.get('incident')}")
            groups.append(
                {
                    "group": g.get("name"),
                    "type": "record" if r.get("record") else "alert",
                    "name": r.get("record") or r.get("alert"),
                    "expr": r.get("expr", "").strip(),
                    "severity": labels.get("severity"),
                    "slo": labels.get("slo") or labels.get("incident"),
                    "for": r.get("for", ""),
                }
            )
    slo_detail = {
        "availability": {"target": ">= 99.9% / 30d", "rule": "SLOAvailabilityBreach", "matcher": 'up{part_of="devops-platform"}'},
        "latency_p95": {"target": "< 200ms", "rule": "SLOLatencyP95Breach", "matcher": "histogram_quantile on duration"},
        "error_rate_5xx": {"target": "< 1%", "rule": "SLO5xxErrorRateBreach", "matcher": "http_requests_total 5xx"},
    }
    service_monitor = {
        "kind": "ServiceMonitor",
        "name": "devops-platform-apps",
        "match_labels": {"app.kubernetes.io/part-of": "devops-platform"},
        "scrape_interval": "15s",
        "scrape_path": "/metrics",
        "relabel": "part_of label exported for SLO matcher",
    }
    prom_found = next((d for d in load_yaml_docs("k8s/apps/chart/templates/servicemonitor.yaml")
                       if d.get("kind") == "ServiceMonitor"), {})
    if prom_found:
        service_monitor["rendered"] = True
    dashboards = sorted(
        p.name for p in (ROOT / "k8s/monitoring/grafana/dashboards").glob("*.yaml")
    ) if (ROOT / "k8s/monitoring/grafana/dashboards").is_dir() else []
    return {
        "slo_detail": slo_detail,
        "rules": groups,
        "slo_labels": sorted(slo_labels),
        "service_monitor": service_monitor,
        "dashboards": dashboards,
    }


def parse_argocd() -> dict[str, Any]:
    docs = load_yaml_docs("k8s/argocd/applicationset.yaml")
    apps = next((d for d in docs if d.get("kind") == "ApplicationSet"), {})
    generators = apps.get("spec", {}).get("generators") or [{}]
    git_gens = [g.get("git", {}) for g in generators if isinstance(g, dict) and g.get("git")]
    git_gen = git_gens[0] if git_gens else {}
    files_pattern: list[dict[str, Any]] = []
    for g in git_gens:
        files_pattern.extend(g.get("files") or [])
    app_manifests = []
    if (ROOT / "k8s/argocd/applications").is_dir():
        for p in sorted((ROOT / "k8s/argocd/applications").glob("*.yaml")):
            for d in _load_yaml_docs(p):
                if d.get("kind") == "Application":
                    app_manifests.append(
                        {
                            "name": (d.get("metadata") or {}).get("name"),
                            "repo": (d.get("spec", {}).get("source", {}).get("repoURL") or "").split("/")[-1],
                            "path": d.get("spec", {}).get("source", {}).get("path"),
                        }
                    )
    tmpl = apps.get("spec", {}).get("template", {})
    helm_params = (tmpl.get("spec", {}).get("source", {}).get("helm", {}).get("parameters") or [])
    markers = _list_service_markers()
    return {
        "name": (apps.get("metadata") or {}).get("name"),
        "namespace": (apps.get("metadata") or {}).get("namespace"),
        "generator_type": "files" if files_pattern else "git",
        "repo_url": git_gen.get("repoURL"),
        "revision": git_gen.get("revision"),
        "files_pattern": [f.get("path") for f in files_pattern if not f.get("exclude")],
        "app_name_template": tmpl.get("metadata", {}).get("name"),
        "helm_tag_param": any(p.get("name") == "services[0].tag" for p in helm_params),
        "markers": [{"name": m["name"], "tag": m.get("tag"), "path": m["path"]} for m in markers],
        "scoped_to": "1 Application per app/<svc>/service.yaml marker, by file content (YAML)",
        "sync_policy": (tmpl.get("spec", {}) or {}).get("syncPolicy"),
        "static_applications": app_manifests,
        "part_of_label": (apps.get("metadata", {}).get("labels", {})).get("app.kubernetes.io/part-of"),
    }


# ──────────────────────────────────────────────────────────────
# Console actions — create/delete apps and services on disk.
# The platform is discovery-driven, so creating/removing these
# files is the ONLY change needed: CI, Helm, Vault, ArgoCD and
# monitoring adapt by themselves.
# ──────────────────────────────────────────────────────────────

SERVICE_TEMPLATE = '''import os
import secrets

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from shared.log_config import setup_logging
from shared.vault_client import get_secret, SecretUnavailable, vault_health

_LOG = setup_logging("{service}")

app = FastAPI(title="{service}", version="1.0.0")

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_respect_env_var=False,
    excluded_handlers=["/livez", "/readyz", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def _load_secrets() -> None:
    global DATABASE_URL, JWT_SECRET_KEY
    is_dev = os.environ.get("ENVIRONMENT", "production").lower() in ("dev", "development", "local")
    try:
        DATABASE_URL = get_secret("DATABASE_URL")
    except SecretUnavailable:
        DATABASE_URL = None
    try:
        JWT_SECRET_KEY = get_secret("JWT_SECRET_KEY")
    except SecretUnavailable:
        JWT_SECRET_KEY = None
    if not DATABASE_URL:
        if is_dev:
            DATABASE_URL = "sqlite:///file::memory:?cache=shared&uri=true"
        else:
            raise SystemExit("DATABASE_URL is missing in Vault; refusing to start.")
    if not JWT_SECRET_KEY:
        if is_dev:
            JWT_SECRET_KEY = secrets.token_hex(32)
        else:
            raise SystemExit("JWT_SECRET_KEY missing in Vault; refusing to start.")


try:
    _load_secrets()
except SecretUnavailable as exc:
    _LOG.error("startup.secret_unavailable", extra={{"event": "startup.secret_unavailable", "error": str(exc)}})
    raise SystemExit(str(exc)) from exc

SERVICE_NAME = os.environ.get("SERVICE_NAME", "{service}")
VAULT_CONFIGURED = bool(os.environ.get("VAULT_ADDR"))


@app.get("/")
def root():
    return {{"service": "{service}", "version": "1.0.0", "vault_configured": VAULT_CONFIGURED}}


@app.get("/livez")
def livez():
    return {{"status": "alive"}}


@app.get("/readyz")
def readyz():
    health = vault_health()
    return JSONResponse(
        status_code=200 if health.get("reachable") else 503,
        content={{"service": "{service}", "vault": health}},
    )
'''

REQUIREMENTS_TEMPLATE = (
    "fastapi==0.139.0\n"
    "uvicorn[standard]>=0.24.0\n"
    "prometheus-fastapi-instrumentator==6.1.0\n"
    "hvac==2.1.0\n"
)


def _svc_dir(app_name: str, svc_name: str) -> Path:
    if app_name in (None, "", DEFAULT_APP):
        return APP_DIR / svc_name
    return APP_DIR / app_name / svc_name


def _validate_slug(name: str, what: str) -> None:
    if not SLUG_RE.match(name or ""):
        raise ServiceError(f"{what} '{name}' invalid — use lowercase letters, digits, single dashes (DNS-1123), max 40 chars")


def create_app(name: str) -> dict[str, Any]:
    _validate_slug(name, "app")
    target = APP_DIR / name
    if target.exists():
        raise ServiceError(f"app '{name}' already exists")
    target.mkdir()
    return {"ok": True, "message": f"app '{name}' created at app/{name}/"}


def delete_app(name: str) -> dict[str, Any]:
    if name == DEFAULT_APP:
        flat = [d for d in _iter_service_dirs() if d[0] is None]
        if flat:
            raise ServiceError(
                f"'{DEFAULT_APP}' is the flat legacy group and still holds {len(flat)} service(s) — delete them first"
            )
        return {"ok": True, "message": "default group is already empty"}
    target = APP_DIR / name
    if not target.is_dir():
        raise ServiceError(f"app '{name}' does not exist")
    shutil.rmtree(target)
    return {**sync_shared_app(), "message": f"app '{name}' and its services deleted (cascade)"}


def create_service(app_name: str, svc_name: str) -> dict[str, Any]:
    _validate_slug(svc_name, "service")
    if app_name not in (None, "", DEFAULT_APP):
        _validate_slug(app_name, "app")
        parent = APP_DIR / app_name
        if not parent.is_dir():
            raise ServiceError(f"app '{app_name}' does not exist — create it first")
    svc_dir = _svc_dir(app_name, svc_name)
    if svc_dir.exists():
        raise ServiceError(f"service '{svc_name}' already exists")
    svc_dir.mkdir(parents=True)
    k8s = _k8s_name(None if app_name in (None, "", DEFAULT_APP) else app_name, svc_dir)
    (svc_dir / SERVICE_MARKER).write_text(SERVICE_TEMPLATE.format(service=k8s))
    (svc_dir / "requirements.txt").write_text(REQUIREMENTS_TEMPLATE)
    _write_service_marker(app_name, svc_dir)
    sync_shared_app()
    return {
        "ok": True,
        "message": f"service '{k8s}' created at {svc_dir.relative_to(ROOT)}/ — CI will build, ArgoCD will deploy, Vault will provision",
    }


def delete_service(app_name: str, svc_name: str) -> dict[str, Any]:
    svc_dir = _svc_dir(app_name, svc_name)
    if not (svc_dir / SERVICE_MARKER).is_file():
        raise ServiceError(f"service '{svc_name}' not found in app '{app_name or DEFAULT_APP}'")
    k8s = _k8s_name(None if app_name in (None, "", DEFAULT_APP) else app_name, svc_dir)
    shutil.rmtree(svc_dir)
    sync_shared_app()
    return {
        "ok": True,
        "message": f"service '{k8s}' deleted — CI matrix shrinks, ArgoCD prunes, Vault role idles",
    }


# ──────────────────────────────────────────────────────────────
# Ship flow — the golden path made visible. create_service() only
# writes files; shipping means branch → commit → push → PR, and the
# pipeline tracker names the FIRST stage a service is stuck on.
# ──────────────────────────────────────────────────────────────

SHARED_APP_PATH = ROOT / "k8s/argocd/applications/shared-app.yaml"


def sync_shared_app() -> dict[str, Any]:
    """Rewrite the services[i].name Helm params of the devops-platform-shared
    Application to match discover_services(). Generated, never hand-maintained —
    create/delete service call this automatically so the shared RoleBinding
    subjects stay complete."""
    path = SHARED_APP_PATH
    if not path.is_file():
        raise ServiceError(f"{path.relative_to(ROOT)} not found — cannot sync shared app")
    try:
        docs = list(yaml.safe_load_all(path.read_text()))
    except yaml.YAMLError as exc:
        raise ServiceError(f"cannot parse {path.relative_to(ROOT)}: {exc}") from exc
    target = next(
        (d for d in docs
         if isinstance(d, dict) and d.get("kind") == "Application"
         and (d.get("metadata") or {}).get("name") == "devops-platform-shared"),
        None,
    )
    if target is None:
        raise ServiceError("devops-platform-shared Application not found in shared-app.yaml")
    params = target.setdefault("spec", {}).setdefault("source", {}).setdefault("helm", {}).setdefault("parameters", [])
    keep = [p for p in params if not str(p.get("name") or "").startswith("services[")]
    for i, svc in enumerate(discover_services()):
        keep.append({"name": f"services[{i}].name", "value": svc})
    target["spec"]["source"]["helm"]["parameters"] = keep
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False))
    return {"ok": True, "message": f"shared app services synced ({len(discover_services())} services)"}


def ship_service(app_name: str, svc_name: str, open_pr: bool = True) -> dict[str, Any]:
    """MUTATING — create_service() + git branch/commit/push + `gh pr create`.
    Nothing lands on `secondary` unreviewed. Leaves the local repo on the new
    branch (subsequent commits to this service belong to the PR)."""
    _validate_slug(svc_name, "service")
    if app_name not in (None, "", DEFAULT_APP):
        _validate_slug(app_name, "app")
    svc_dir = _svc_dir(app_name, svc_name)
    k8s = _k8s_name(None if app_name in (None, "", DEFAULT_APP) else app_name, svc_dir)
    branch = f"service/{k8s}"
    rel = f"app/{svc_dir.relative_to(APP_DIR)}"

    create_service(app_name, svc_name)

    res = _run(["git", "checkout", "-b", branch], timeout=15)
    if not res["ok"]:
        raise ServiceError(res["stderr"] or f"could not create branch {branch}")
    res = _run(["git", "add", rel], timeout=15)
    if not res["ok"]:
        raise ServiceError(res["stderr"] or "git add failed")
    res = _run(
        ["git", "commit", "-m", f"feat({k8s}): scaffold new service from platform UI"],
        timeout=15,
    )
    if not res["ok"]:
        raise ServiceError(res["stderr"] or "git commit failed — nothing to commit?")
    res = _run(["git", "push", "-u", "origin", branch], timeout=GH_TIMEOUT)
    if not res["ok"]:
        raise ServiceError(res["stderr"] or f"git push failed for {branch}")

    pr_url = None
    if open_pr:
        body = (
            f"Ship service `{k8s}` from the platform UI.\n\n"
            f"Files: `{rel}/` (`main.py`, `requirements.txt`, `service.yaml` marker).\n"
            "Once merged, CI builds the image, tag-backfill pins the marker to "
            "`commit-<sha7>`, ArgoCD's ApplicationSet picks it up, and the Vault "
            "setup job provisions `secret/devops-platform/" + k8s + "`."
        )
        pr = _run(
            ["gh", "pr", "create", "--repo", _repo_slug(), "--head", branch,
             "--base", "secondary", "--title", f"feat: ship service {k8s}", "--body", body],
            timeout=GH_TIMEOUT,
        )
        if not pr["ok"]:
            raise ServiceError(pr["stderr"] or "gh pr create failed")
        pr_url = pr["stdout"].strip()

    return {
        "ok": True,
        "message": f"service '{k8s}' shipped on branch {branch}",
        "branch": branch,
        "pr_url": pr_url,
        "pipeline": service_pipeline(k8s),
    }


def seed_service_secrets(service: str) -> dict[str, Any]:
    """MUTATING — write DATABASE_URL + JWT_SECRET_KEY into Vault for one
    service. Never returns the values (see conventions)."""
    token = _vault_root_token()
    if not token:
        raise ServiceError("could not read vault-root-token secret")
    db = f"postgresql://app:{secrets_module().token_hex(12)}@postgres-{service}.devops-platform.svc.cluster.local:5432/{service}"
    jw = secrets_module().token_hex(32)
    res = _run(
        ["kubectl", "exec", "-n", "vault", "deploy/vault", "--",
         "env", f"VAULT_TOKEN={token}", "vault", "kv", "put",
         f"secret/devops-platform/{service}", f"DATABASE_URL={db}", f"JWT_SECRET_KEY={jw}"],
        timeout=15,
    )
    if not res["ok"]:
        raise ServiceError(res["stderr"] or "vault kv put failed")
    return {"ok": True, "message": f"seeded secret/devops-platform/{service} (DATABASE_URL, JWT_SECRET_KEY)"}


def sync_service_list() -> dict[str, Any]:
    """MUTATING — rewrite the devops-service-list ConfigMap (vault ns) from
    discover_services(). This is the configmap the vault-setup Job reads."""
    services = discover_services()
    rendered = _run(
        ["kubectl", "create", "configmap", "devops-service-list", "-n", "vault",
         "--from-literal=services=" + " ".join(services),
         "--dry-run=client", "-o", "yaml"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not rendered["ok"]:
        raise ServiceError(rendered["stderr"] or "could not render configmap")
    applied = _run(["kubectl", "apply", "-f", "-"], timeout=KUBECTL_TIMEOUT, input_=rendered["stdout"])
    if not applied["ok"]:
        raise ServiceError(applied["stderr"] or "could not apply configmap")
    return {"ok": True, "message": f"devops-service-list synced with {len(services)} services", "services": services}


def rerun_vault_setup() -> dict[str, Any]:
    """MUTATING — force the vault-setup Job to re-run: delete it (TTL expired
    jobs are gone from the API) then re-apply the manifests."""
    _run(["kubectl", "delete", "job", "vault-setup-job", "-n", "vault", "--ignore-not-found"], timeout=KUBECTL_TIMEOUT)
    res = _run(["kubectl", "apply", "-f", str(ROOT / "k8s/vault/manifests.yaml")], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        raise ServiceError(res["stderr"] or "could not apply vault manifests")
    return {"ok": True, "message": "vault-setup-job re-created — provisioning all services"}


def secrets_module():
    import secrets

    return secrets


def _pipeline_result(service: str, stages: list[dict[str, Any]]) -> dict[str, Any]:
    blocker = next((s for s in stages if s["state"] != "ok"), None)
    return {
        "service": service,
        "stages": stages,
        "blocking": blocker["stage"] if blocker else None,
        "all_ok": blocker is None,
        "blocking_detail": blocker["detail"] if blocker else None,
    }


def _vault_secret_ok(service: str) -> dict[str, Any]:
    """Both DATABASE_URL and JWT_SECRET_KEY must exist AND be non-empty.
    Reads values to check emptiness but never returns them."""
    token = _vault_root_token()
    if not token:
        return {"reachable": False, "error": "could not read vault-root-token secret"}
    res = _run(
        ["kubectl", "exec", "-n", "vault", "deploy/vault", "--",
         "env", f"VAULT_TOKEN={token}", "vault", "kv", "get", "-format=json",
         f"secret/devops-platform/{service}"],
        timeout=10,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "cannot read secret"}
    try:
        data = json.loads(res["stdout"]).get("data", {}).get("data", {})
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse vault output"}
    present = {k: bool(data.get(k)) for k in ("DATABASE_URL", "JWT_SECRET_KEY")}
    return {"reachable": True, "present": all(present.values()), "missing": sorted(k for k, v in present.items() if not v)}


def _deployment_ready(namespace: str, name: str) -> dict[str, Any]:
    res = _run(
        ["kubectl", "get", "deploy", name, "-n", namespace,
         "-o", "jsonpath={.status.readyReplicas}/{.spec.replicas}/{.status.availableReplicas}"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "deployment not found"}
    parts = res["stdout"].split("/")
    if len(parts) != 3:
        return {"reachable": False, "error": f"unexpected output: {res['stdout']}"}
    try:
        ready, desired, available = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return {"reachable": False, "error": f"unexpected output: {res['stdout']}"}
    return {"reachable": True, "ready": ready, "desired": desired, "available": available}


def _ci_build_conclusion(branch: str, service: str = "") -> dict[str, Any]:
    """Latest CI run on the branch + its Build & Push Images job conclusion.
    The gh token lacks read:packages, so a registry HEAD check returns 403 —
    the CI build job conclusion is the honest image-exists signal.
    Build jobs carry matrix-suffixed names ("Build & Push Images (svc, path)"),
    so the filter matches by prefix — the run-level conclusion already
    aggregates all matrix rows."""
    res = _run(
        ["gh", "run", "list", "--repo", _repo_slug(), "--branch", branch, "--limit", "1", "--json",
         "databaseId,status,conclusion"],
        timeout=GH_TIMEOUT,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "gh CLI unavailable"}
    try:
        runs = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse gh output"}
    if not runs:
        return {"reachable": True, "run_id": None, "build_ok": None, "detail": "no runs on this branch yet"}
    run = runs[0]
    if run.get("status") != "completed":
        return {"reachable": True, "run_id": run.get("databaseId"), "build_ok": None, "detail": f"latest run is {run.get('status')}"}
    if run.get("conclusion") != "success":
        return {"reachable": True, "run_id": run.get("databaseId"), "build_ok": False, "detail": f"latest run concluded {run.get('conclusion')}"}
    jobs = _run(
        ["gh", "run", "view", str(run.get("databaseId")), "--repo", _repo_slug(),
         "--json", "jobs", "--jq", '.jobs[] | select(.name | startswith("Build & Push Images")) | .conclusion'],
        timeout=GH_TIMEOUT,
    )
    if not jobs["ok"] or not jobs["stdout"]:
        return {"reachable": True, "run_id": run.get("databaseId"), "build_ok": None, "detail": "run green but build job conclusion unavailable"}
    return {"reachable": True, "run_id": run.get("databaseId"), "build_ok": jobs["stdout"].splitlines()[-1].strip() == "success", "detail": jobs["stdout"].splitlines()[-1].strip()}


def service_pipeline(service: str) -> dict[str, Any]:
    """Ordered end-to-end stage tracker. Each stage: {stage, state,
    detail}. The first non-ok stage is the blocker — this is the feature:
    naming exactly where a service is stuck."""
    app_name, svc_dir = _svc_dir_from_k8s_name(service)
    rel = f"app/{svc_dir.relative_to(APP_DIR)}"
    stages: list[dict[str, Any]] = []

    # 1. files on disk
    if not (svc_dir / SERVICE_MARKER).is_file():
        return _pipeline_result(service, [{"stage": "files", "state": "failed", "detail": f"no {rel}/main.py on disk"}])
    stages.append({"stage": "files", "state": "ok", "detail": f"{rel}/main.py + service.yaml present"})

    # 2. committed & pushed (service branch first, then secondary fallback)
    branch = f"service/{service}"
    commits = _git(["log", f"origin/{branch}", "--oneline", "--", rel])
    pushed_source = f"origin/{branch}"
    if not commits:
        commits = _git(["log", "origin/secondary", "--oneline", "--", rel])
        pushed_source = "origin/secondary"
    dirty = _git(["status", "--porcelain", "--", rel])
    if commits and not dirty:
        stages.append({"stage": "committed", "state": "ok", "detail": f"{len(commits.splitlines())} commit(s) on {pushed_source}, tree clean"})
    elif commits and dirty:
        stages.append({"stage": "committed", "state": "failed", "detail": f"pushed on {pushed_source} but local changes uncommitted"})
    else:
        stages.append({"stage": "committed", "state": "failed", "detail": "never pushed — use Ship (branch service/<name> + PR)"})
    if stages[-1]["state"] != "ok":
        return _pipeline_result(service, stages)

    # 3. PR open
    if _git(["rev-parse", f"origin/{branch}"]):
        prs = _run(["gh", "pr", "list", "--repo", _repo_slug(), "--head", branch, "--json", "state"], timeout=GH_TIMEOUT)
        if not prs["ok"]:
            stages.append({"stage": "pr", "state": "failed", "detail": prs["stderr"] or "gh unavailable"})
        else:
            try:
                open_prs = [p for p in json.loads(prs["stdout"]) if p.get("state") == "OPEN"]
                stages.append({"stage": "pr", "state": "ok" if open_prs else "failed", "detail": f"{len(open_prs)} open PR(s) for {branch}" if open_prs else f"branch {branch} exists but no open PR"})
            except json.JSONDecodeError:
                stages.append({"stage": "pr", "state": "failed", "detail": "could not parse gh output"})
    else:
        stages.append({"stage": "pr", "state": "ok", "detail": "no service/<name> branch — shipped directly on secondary"})
    if stages[-1]["state"] != "ok":
        return _pipeline_result(service, stages)

    # 4. CI green + 5. image in GHCR
    ci = _ci_build_conclusion(branch if _git(["rev-parse", f"origin/{branch}"]) else "secondary", service)
    if not ci["reachable"]:
        stages.append({"stage": "ci", "state": "failed", "detail": ci["error"]})
    elif ci["build_ok"] is None:
        stages.append({"stage": "ci", "state": "pending", "detail": ci["detail"]})
    elif not ci["build_ok"]:
        stages.append({"stage": "ci", "state": "failed", "detail": ci["detail"] + " (run " + str(ci["run_id"]) + ")"})
    else:
        stages.append({"stage": "ci", "state": "ok", "detail": f"build job success (run {ci['run_id']})"})
    if stages[-1]["state"] != "ok":
        return _pipeline_result(service, stages)
    stages.append({
        "stage": "image",
        "state": "ok",
        "detail": "gh token lacks read:packages — image presence inferred from the CI build job conclusion, not registry HEAD",
    })

    # 6. Vault secrets present (both keys, non-empty)
    vault = _vault_secret_ok(service)
    if not vault["reachable"]:
        stages.append({"stage": "vault", "state": "failed", "detail": vault["error"]})
    elif not vault["present"]:
        stages.append({"stage": "vault", "state": "failed", "detail": f"missing/empty: {', '.join(vault['missing'])} — Seed secrets"})
    else:
        stages.append({"stage": "vault", "state": "ok", "detail": "DATABASE_URL + JWT_SECRET_KEY present"})
    if stages[-1]["state"] != "ok":
        return _pipeline_result(service, stages)

    # 7. ArgoCD synced
    argo = argocd_apps()
    app = next((a for a in argo.get("apps", []) if a["name"] == service), None) if argo.get("reachable") else None
    if not argo.get("reachable"):
        stages.append({"stage": "argocd", "state": "failed", "detail": argo.get("error", "cluster unreachable")})
    elif app is None:
        stages.append({"stage": "argocd", "state": "pending", "detail": "Application not created yet — ApplicationSet watches the marker"})
    elif app["sync_status"] == "Synced" and app["health_status"] == "Healthy":
        stages.append({"stage": "argocd", "state": "ok", "detail": f"Synced / {app['health_status']} @ {app['revision']}"})
    elif app["sync_status"] == "Synced":
        stages.append({"stage": "argocd", "state": "pending", "detail": f"Synced but health {app['health_status']}"})
    else:
        stages.append({"stage": "argocd", "state": "failed", "detail": f"sync {app['sync_status']} / health {app['health_status']}"})
    if stages[-1]["state"] != "ok":
        return _pipeline_result(service, stages)

    # 8. pods ready
    dep = _deployment_ready(PLATFORM_NAMESPACE, service)
    if not dep["reachable"]:
        stages.append({"stage": "pods", "state": "failed", "detail": dep["error"]})
    elif dep["ready"] == dep["desired"] and dep["desired"] > 0:
        stages.append({"stage": "pods", "state": "ok", "detail": f"{dep['ready']}/{dep['desired']} ready"})
    else:
        stages.append({"stage": "pods", "state": "pending" if dep["ready"] > 0 else "failed", "detail": f"{dep['ready']}/{dep['desired']} ready"})
    if stages[-1]["state"] != "ok":
        return _pipeline_result(service, stages)

    # 9. serving /readyz — asked of the container over its own loopback.
    #
    # Two things made this stage lie about every service. The exec had no -c,
    # so it landed on the `vault-token-refresh` sidecar, whose image has no
    # python: `exec: "python": executable file not found in $PATH`. With that
    # fixed it still failed, because it fetched the ClusterIP Service from
    # inside the pod and this namespace runs a default-deny egress policy —
    # the request timed out even though kubelet's own probe against the same
    # endpoint was passing. 127.0.0.1:<containerPort> asks the process what
    # this stage is actually about: is it serving.
    port = _container_port(PLATFORM_NAMESPACE, service) or 8000
    readyz = _run(
        ["kubectl", "exec", "-n", PLATFORM_NAMESPACE, f"deploy/{service}", "-c", service, "--",
         "python", "-c",
         f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/readyz', timeout=5)"],
        timeout=KUBECTL_TIMEOUT,
    )
    if readyz["ok"]:
        stages.append({"stage": "readyz", "state": "ok", "detail": "GET /readyz → 200"})
    else:
        stages.append({"stage": "readyz", "state": "failed", "detail": (readyz["stderr"] or "readyz not 200").splitlines()[-1] if (readyz["stderr"] or "") else "readyz not 200"})
    return _pipeline_result(service, stages)


# ──────────────────────────────────────────────────────────────
# Infrastructure control (WS-B) — capacity, IaC drift, state
# reconciliation, apply preflight. Reconcile is the only mutation;
# apply is gated behind node_preflight() which refuses with the
# SPECIFIC reason (full disk today) instead of failing halfway.
# ──────────────────────────────────────────────────────────────

TF_DIR = ROOT / "terraform"
TF_STATE = TF_DIR / "terraform.tfstate"
TF_TFVARS = TF_DIR / "terraform.tfvars"
TF_VARS = TF_DIR / "variables.tf"
TF_LOCK = TF_DIR / ".terraform.tfstate.lock.info"


def _cpu_to_millicores(value: str) -> float:
    value = (value or "0").strip()
    if value.endswith("m"):
        return float(value[:-1])
    return float(value) * 1000


def _mem_to_mib(value: str) -> float:
    value = (value or "0").strip().upper()
    units = {"KI": 1 / 1024, "MI": 1, "GI": 1024, "TI": 1024 * 1024, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}
    for suffix, mult in units.items():
        if value.endswith(suffix):
            try:
                return float(value[: -len(suffix)]) * mult
            except ValueError:
                return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def cluster_capacity() -> dict[str, Any]:
    """cores/RAM used vs allocatable + room for N more services at current
    replicas, M if all burst to HPA max. Stated plainly: NO cluster-autoscaler
    exists — on libvirt, capacity is a human action."""
    nodes = _run(["kubectl", "get", "nodes", "-o", "json"], timeout=KUBECTL_TIMEOUT)
    pods = _run(["kubectl", "get", "pods", "-A", "-o", "json"], timeout=KUBECTL_TIMEOUT)
    if not nodes["ok"]:
        return {"reachable": False, "error": nodes["stderr"], "summary": ""}
    try:
        node_data = json.loads(nodes["stdout"])
        pod_data = json.loads(pods["stdout"]) if pods["ok"] else {"items": []}
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output", "summary": ""}

    alloc_cpu = alloc_mem = 0.0
    taints: list[str] = []
    for n in node_data.get("items", []):
        a = n.get("status", {}).get("allocatable", {})
        alloc_cpu += _cpu_to_millicores(a.get("cpu", "0"))
        alloc_mem += _mem_to_mib(a.get("memory", "0"))
        if n.get("spec", {}).get("taints"):
            taints.append(n["metadata"]["name"])

    req_cpu = req_mem = 0.0
    for p in pod_data.get("items", []):
        if p.get("status", {}).get("phase") == "Succeeded":
            continue
        for c in p.get("spec", {}).get("containers", []):
            r = c.get("resources", {}).get("requests", {})
            req_cpu += _cpu_to_millicores(r.get("cpu", "0"))
            req_mem += _mem_to_mib(r.get("memory", "0"))

    hpa_res = _run(["kubectl", "get", "hpa", "-A", "-o", "json"], timeout=KUBECTL_TIMEOUT)
    max_replicas = 5
    min_replicas = 2
    if hpa_res["ok"]:
        try:
            hp = json.loads(hpa_res["stdout"])
            counts = [h.get("spec", {}).get("maxReplicas", 0) for h in hp.get("items", [])]
            if counts:
                max_replicas = max(counts)
        except json.JSONDecodeError:
            pass

    free_cpu = max(alloc_cpu - req_cpu, 0.0)
    free_mem = max(alloc_mem - req_mem, 0.0)
    per_pod_cpu, per_pod_mem = 100.0, 128.0
    room_now = min(int(free_cpu / (per_pod_cpu * min_replicas)), int(free_mem / (per_pod_mem * min_replicas)))
    room_burst = min(int(free_cpu / (per_pod_cpu * max_replicas)), int(free_mem / (per_pod_mem * max_replicas)))
    summary = (
        f"room for {room_now} more services at current replicas (HPA min {min_replicas}), "
        f"{room_burst} if all burst to HPA max {max_replicas}"
    )
    return {
        "reachable": True,
        "allocatable": {"cpu_m": round(alloc_cpu), "memory_mib": round(alloc_mem)},
        "requested": {"cpu_m": round(req_cpu), "memory_mib": round(req_mem)},
        "used_pct": {"cpu": round(req_cpu / alloc_cpu * 100) if alloc_cpu else 0, "memory": round(req_mem / alloc_mem * 100) if alloc_mem else 0},
        "tainted_nodes": taints,
        "hpa_max": max_replicas,
        "room_now": room_now,
        "room_burst": room_burst,
        "no_autoscaler": True,
        "summary": summary,
    }


def _tfvars_keys() -> set[str]:
    try:
        text = TF_TFVARS.read_text()
    except OSError:
        return set()
    return set(re.findall(r"^\s*([a-z_]+)\s*=", text, re.M))


def _tfvars_value(key: str) -> str | None:
    try:
        text = TF_TFVARS.read_text()
    except OSError:
        return None
    m = re.search(rf"^\s*{key}\s*=\s*([^\n]+)", text, re.M)
    return m.group(1).strip().strip('"') if m else None


def _declared_tf_vars() -> set[str]:
    try:
        text = TF_VARS.read_text()
    except OSError:
        return set()
    return set(re.findall(r'variable\s+"([a-z_]+)"', text))


def _tfstate_resources() -> dict[tuple[str, str], list[dict[str, Any]]]:
    """(type, name) → list of instances (with attributes) in state."""
    try:
        data = json.loads(TF_STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in data.get("resources", []):
        out[(r.get("type"), r.get("name"))] = r.get("instances", [])
    return out


def _terraform_plan() -> dict[str, Any]:
    res = _run(
        ["terraform", "plan", "-input=false", "-detailed-exitcode", "-no-color"],
        timeout=90, cwd=str(TF_DIR),
    )
    # -detailed-exitcode: 0 = no changes, 2 = changes (valid plan), 1 = error.
    no_changes = "No changes." in res["stdout"]
    return {
        "ok": res.get("code") in (0, 2) or no_changes,
        "empty": no_changes,
        "error": res["stderr"] if res.get("code") == 1 else "",
        "stdout": res["stdout"],
    }


def terraform_drift() -> dict[str, Any]:
    """Sibling of drift_report(): what terraform state thinks vs. what's
    really running. Surfaces today's three findings: 1-of-3 VMs in state,
    undeclared AWS vars, stale lock."""
    findings = []

    state = _tfstate_resources()
    domains = [i.get("attributes", {}).get("name") for (t, n), insts in state.items() if t == "libvirt_domain" for i in insts]
    virsh = _run(["virsh", "list", "--all", "--name"], timeout=10)
    running = [line.strip() for line in virsh["stdout"].splitlines() if line.strip()] if virsh["ok"] else []
    missing_from_state = sorted(set(running) - set(domains))
    stale_in_state = sorted(set(domains) - set(running))

    declared = _declared_tf_vars()
    used = _tfvars_keys()
    undeclared = sorted(used - declared)

    stale_lock = TF_LOCK.is_file()

    if missing_from_state:
        findings.append(f"{len(missing_from_state)} VM(s) running but missing from state: {', '.join(missing_from_state)}")
    if stale_in_state:
        findings.append(f"{len(stale_in_state)} VM(s) in state but not running: {', '.join(stale_in_state)}")
    if undeclared:
        findings.append(f"{len(undeclared)} tfvars key(s) not declared in variables.tf: {', '.join(undeclared)}")
    if stale_lock:
        findings.append("stale .terraform.tfstate.lock.info blocks every terraform command")

    plan = _terraform_plan() if (TF_STATE.is_file() and (TF_DIR / ".terraform").is_dir()) else {"ok": False, "empty": False, "error": "terraform not initialized (run reconcile)", "stdout": ""}
    if plan["ok"] and not plan["empty"]:
        findings.append(
            "terraform plan is not empty — remaining diffs are attrs the libvirt "
            "provider 0.9.8 cannot import (cloudinit_disk, network dns/domain/ips, "
            "volume create/source)"
        )
    if not plan["ok"] and plan.get("error"):
        findings.append(f"terraform plan failed: {plan['error']}")

    return {
        "reachable": True,
        "domains_in_state": domains,
        "vms_running": running,
        "missing_from_state": missing_from_state,
        "stale_in_state": stale_in_state,
        "undeclared_vars": undeclared,
        "stale_lock": stale_lock,
        "plan_empty": plan["empty"] if plan.get("ok") else None,
        "findings": findings,
    }


def _node_names() -> list[str]:
    count = int(_tfvars_value("worker_count") or 2)
    master = _tfvars_value("master_name") or "master-01"
    return [master] + [f"worker-{i:02d}" for i in range(1, count + 1)]


def _terraform_imports_needed() -> list[tuple[str, str]]:
    """(import_address, id) pairs for every resource main.tf declares but
    state lacks. Generated from main.tf's for_each keys — never hardcoded."""
    try:
        main = (TF_DIR / "main.tf").read_text()
    except OSError:
        return []
    state = _tfstate_resources()
    nodes = _node_names()
    for_each_resources: dict[str, str] = {}
    for m in re.finditer(r'resource\s+"([a-z_]+)"\s+"([a-z_]+)"\s*\{', main):
        rtype, rname = m.group(1), m.group(2)
        rest = main[m.end():]
        nxt = re.search(r"\nresource\s+\"", rest)
        block = rest if nxt is None else rest[:nxt.start()]
        for_each_resources[(rtype, rname)] = block if "for_each" in block else ""

    imports: list[tuple[str, str]] = []
    for (rtype, rname), head in for_each_resources.items():
        if rtype in ("terraform_data", "libvirt_cloudinit_disk", "local_file"):
            # Not importable by their providers (0.9.8 libvirt / 2.9.0 local):
            # terraform_data is an ephemeral meta-resource; cloudinit_disk has
            # no import support at all; local_file only materializes on apply
            # (its content is regenerated from inventory.tpl — non-destructive).
            continue
        if "for_each" in head:
            for node in nodes:
                addr = f'{rtype}.{rname}["{node}"]'
                if (rtype, rname) not in state:
                    id_value = _import_id(rtype, rname, node)
                    imports.append((addr, id_value))
        elif (rtype, rname) not in state:
            id_value = _import_id(rtype, rname, None)
            imports.append((f"{rtype}.{rname}", id_value))
    return imports


def _pool_path() -> str:
    """Directory of the libvirt storage pool — volume import IDs are the
    full pool-relative key (e.g. /var/lib/libvirt/images/x.qcow2), not the
    bare name the provider's create uses."""
    pool = _tfvars_value("storage_pool") or "default"
    res = _run(["virsh", "pool-dumpxml", pool], timeout=10)
    m = re.search(r"<path>([^<]+)</path>", res.get("stdout", ""))
    return m.group(1) if m else "/var/lib/libvirt/images"


def _import_id(rtype: str, rname: str, node: str | None) -> str:
    if rtype == "libvirt_domain":
        return node or ""
    if rtype == "libvirt_network":
        return _tfvars_value("network_name") or "devops-platform-net"
    if rtype == "local_file":
        return str(TF_DIR / "inventory.generated.ini")
    if rtype == "libvirt_cloudinit_disk":
        return f"{node}-init.iso"
    if rtype == "libvirt_volume":
        name = {
            "base": _tfvars_value("base_image_name") or "ubuntu-base.qcow2",
            "cloudinit_iso": f"{node}-cloudinit.iso",
        }.get(rname, f"{node}.qcow2")
        return f"{_pool_path()}/{name}"
    return node or rname


def _tfvars_conformance() -> list[str]:
    declared = _declared_tf_vars()
    used = _tfvars_keys()
    return sorted(used - declared)


def terraform_reconcile() -> dict[str, Any]:
    """MUTATING — make state honest: clear the stale lock, replace the
    AWS-shaped tfvars with libvirt-correct values matching the running
    cluster, import every resource the provider supports, then run plan.
    Non-empty plans are reported with the provider-import gap that causes
    them — apply is never run from here (destructive by design)."""
    steps: list[str] = []

    if TF_LOCK.is_file():
        try:
            TF_LOCK.unlink()
            steps.append("removed stale .terraform.tfstate.lock.info")
        except OSError as exc:
            raise ServiceError(f"cannot remove stale lock: {exc}") from exc

    tfvars = {
        "worker_count": _tfvars_value("worker_count") or "2",
        "vm_vcpu": _tfvars_value("vm_vcpu") or "2",
        "vm_memory_mb": _tfvars_value("vm_memory_mb") or "4096",
        "disk_size_gb": _tfvars_value("disk_size_gb") or "20",
        "ssh_user": _tfvars_value("ssh_user") or SSH_USER,
        "network_cidr": _tfvars_value("network_cidr") or NETWORK_CIDR,
        "master_name": _tfvars_value("master_name") or K8S_MASTER_NAME,
    }
    try:
        TF_TFVARS.write_text(
            "".join(f'{k} = "{v}"\n' for k, v in tfvars.items())
        )
        steps.append("rewrote terraform.tfvars to libvirt-correct values (devops/2 workers)")
    except OSError as exc:
        raise ServiceError(f"cannot rewrite tfvars: {exc}") from exc

    if not (TF_DIR / ".terraform").is_dir():
        init = _run(["terraform", "init", "-input=false", "-no-color"], timeout=90, cwd=str(TF_DIR))
        if not init["ok"]:
            raise ServiceError(init["stderr"] or "terraform init failed")
        steps.append("terraform init")

    imports = _terraform_imports_needed()
    for addr, id_value in imports:
        imp = _run(
            ["terraform", "import", "-input=false", "-no-color", addr, id_value],
            timeout=60, cwd=str(TF_DIR),
        )
        if not imp["ok"]:
            raise ServiceError(f"terraform import {addr} failed: {imp['stderr'] or 'unknown error'}")
        steps.append(f"imported {addr}")

    plan = _terraform_plan()
    if not plan["ok"]:
        raise ServiceError(f"terraform plan failed after reconcile: {plan['error']}")

    imported = [a for a, _ in imports]
    if plan["empty"]:
        message = "state reconciled; terraform plan is empty"
    else:
        message = (
            "state reconciled; plan is NOT empty — remaining diffs are resources the "
            "providers cannot import (libvirt 0.9.8: libvirt_cloudinit_disk has no "
            "import support, network dns/domain/ips and volume create/source are "
            "import-blind; local 2.9.0: local_file only materializes on apply). "
            "Apply refused by safety design — the libvirt diffs would destroy+recreate "
            "live VMs."
        )
    return {"ok": True, "steps": steps, "message": message, "plan_empty": plan["empty"], "imported": imported}


def node_preflight(disk_gb: int | None = None, mem_mb: int | None = None) -> dict[str, Any]:
    """Checks BEFORE any terraform apply. Refuses with the specific reason —
    the refusal itself is the deliverable when the host can't support a new VM."""
    disk_gb = disk_gb or int(_tfvars_value("disk_size_gb") or 20)
    mem_mb = mem_mb or int(_tfvars_value("vm_memory_mb") or 4096)
    problems: list[str] = []
    free_gb: int | None = None

    if TF_LOCK.is_file():
        problems.append("stale .terraform.tfstate.lock.info present — run Reconcile state")

    plan = _terraform_plan() if (TF_DIR / ".terraform").is_dir() else {"ok": False, "empty": False, "error": ""}
    if plan["ok"] and not plan["empty"]:
        problems.append(
            "terraform plan is not empty — remaining diffs are attrs the libvirt "
            "provider 0.9.8 cannot import (cloudinit_disk, network dns/domain/ips, "
            "volume create/source); run Reconcile state to confirm"
        )
    if not plan["ok"] and (TF_DIR / ".terraform").is_dir():
        problems.append(f"terraform plan failed: {plan['error']}")

    try:
        images_dir = Path("/var/lib/libvirt/images")
        free = shutil.disk_usage(images_dir if images_dir.is_dir() else "/").free
        free_gb = free // (1024 ** 3)
        if free_gb < disk_gb:
            problems.append(f"host free disk {free_gb}GB < requested disk_size_gb {disk_gb}GB")
    except OSError as exc:
        problems.append(f"cannot stat host disk: {exc}")

    try:
        with open("/proc/meminfo") as f:
            text = f.read()
        m = re.search(r"MemAvailable:\s+(\d+) kB", text)
        avail_mb = int(m.group(1)) / 1024 if m else 0
        if avail_mb < mem_mb:
            problems.append(f"host available RAM {avail_mb:.0f}MiB < vm_memory_mb {mem_mb}")
    except OSError:
        problems.append("cannot read /proc/meminfo")

    if MASTER_SSH_TARGET:
        join = _run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
             MASTER_SSH_TARGET, "test -f /tmp/kubeadm-join.txt && find /tmp/kubeadm-join.txt -mmin -1440 | grep -q . && echo OK"],
            timeout=10,
        )
        if not join["ok"] or "OK" not in join["stdout"]:
            problems.append("missing/expired /tmp/kubeadm-join.txt on master — regenerate with `kubeadm token create --print-join-command`")
    else:
        problems.append("MASTER_IP not set in .env — ssh preflight skipped")

    return {
        "ok": not problems,
        "problems": problems,
        "disk_gb": disk_gb,
        "mem_mb": mem_mb,
        "free_disk_gb": free_gb,
    }


def _service_pipelines(services: list[str]) -> dict[str, Any]:
    """``{service: service_pipeline(service)}``, computed in parallel.

    Kept synchronous so every existing caller is unaffected; the concurrency
    is threads rather than asyncio because the work is subprocesses, which
    release the GIL while they run. Bounded so a large monorepo cannot spawn
    an unbounded pile of git/gh/kubectl processes at once. A service whose
    pipeline raises is reported as blocked rather than taking the whole
    overview down with it.
    """
    if not services:
        return {}

    def _one(service: str) -> tuple[str, dict[str, Any]]:
        try:
            return service, service_pipeline(service)
        except Exception as exc:  # noqa: BLE001 - one bad service must not blank the console
            return service, {
                "service": service,
                "stages": [],
                "all_ok": False,
                "blocking": "pipeline",
                "blocking_detail": f"could not read pipeline: {exc}",
            }

    with ThreadPoolExecutor(max_workers=min(len(services), _PIPELINE_MAX_WORKERS)) as pool:
        return dict(pool.map(_one, services))


def platform_overview(services: list[str]) -> dict[str, Any]:
    base = {
        "revision": git_info(),
        "repo": str(ROOT),
        "services": services,
        "service_count": len(services),
        "app_count": len(discover_apps()),
        "layer_checks": {
            "dockerfile": (ROOT / "app/Dockerfile").is_file(),
            "helm_chart": (ROOT / "k8s/apps/chart").is_dir(),
            "ci_workflow": (ROOT / ".github/workflows/ci-cd.yml").is_file(),
            "vault": (ROOT / "k8s/vault/manifests.yaml").is_file(),
            "monitoring": (ROOT / "k8s/monitoring/alertmanager/rules.yaml").is_file(),
            "argocd": (ROOT / "k8s/argocd/applicationset.yaml").is_file(),
        },
    }
    # Honest status: outcome roll-up over the per-service pipeline, not
    # file-existence booleans. "healthy" only when every service reaches
    # pods-ready; otherwise "degraded" naming the first blocking stage.
    # One service_pipeline() is roughly seven external calls (git, gh, vault,
    # ArgoCD, kubectl), each with its own timeout. Serially that made this
    # function — and every console view built on it — cost the sum over all
    # services: 26-36s for five, and worse whenever one of those systems was
    # slow, which is exactly when an operator most wants the page. The calls
    # are subprocess-bound, so threads parallelise them well and the cost
    # collapses to roughly one service's worth.
    pipelines = _service_pipelines(services)
    healthy = bool(services) and all(p["all_ok"] for p in pipelines.values())
    blockers = [
        {"service": s, "stage": p["blocking"], "detail": p["blocking_detail"]}
        for s, p in pipelines.items() if not p["all_ok"]
    ]
    return {
        **base,
        "status": "healthy" if healthy else "degraded",
        "pipelines": {
            s: {"blocking": p["blocking"], "all_ok": p["all_ok"], "stage_count": len(p["stages"])}
            for s, p in pipelines.items()
        },
        "blockers": blockers,
    }


# ──────────────────────────────────────────────────────────────
# LIVE STATUS + ACTIONS — real calls against GitHub, the cluster,
# ArgoCD (as K8s CRDs) and Vault. Every function fails soft: on
# timeout/unreachable it returns {"reachable": False, "error": ...}
# instead of raising, so the UI can render an honest offline state.
# ──────────────────────────────────────────────────────────────

KUBECTL_TIMEOUT = 6
GH_TIMEOUT = 25


# Colour codes any tool may emit; stripped from everything the console shows.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _run(cmd: list[str], timeout: int = KUBECTL_TIMEOUT, input_: str | None = None, cwd: str | None = None) -> dict[str, Any]:
    """Run a command and return its output with ANSI colour codes removed.

    Stripping happens here rather than at each call site because everything
    captured this way ends up rendered as text in the console. Some tools
    colour their output whenever they think a terminal is watching, and
    terraform in particular ignores that heuristic for errors — its preflight
    failure reached the UI as `ESC[31mError: ...` and painted the panel with
    literal `[31m` fragments around every word.
    """
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=cwd or str(ROOT), input=input_,
        )
        return {
            "ok": out.returncode == 0,
            "stdout": _ANSI_RE.sub("", out.stdout),
            "stderr": _ANSI_RE.sub("", out.stderr).strip(),
            "code": out.returncode,
        }
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": f"{cmd[0]} not found on PATH", "code": -1}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"{cmd[0]} timed out after {timeout}s", "code": -1}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "stdout": "", "stderr": str(exc), "code": -1}


def _repo_slug() -> str:
    url = _git(["remote", "get-url", "origin"])
    url = url.removesuffix(".git")
    if url.startswith("git@"):
        return url.split(":", 1)[-1]
    return "/".join(url.split("/")[-2:])


def repo_slug_from_url(repo_url: str) -> str:
    """``https://github.com/owner/repo.git`` → ``owner/repo``.

    The tenant-facing CI views resolve the slug from the *deployment's own*
    repo_url, never from ``_repo_slug()`` — that one reads this control
    plane's own git remote, so using it for a tenant view would show every
    user the platform's pipeline instead of their own app's.
    """
    url = (repo_url or "").strip().removesuffix(".git")
    if url.startswith("git@"):
        url = url.split(":", 1)[-1]
    parts = [p for p in url.split("/") if p]
    if len(parts) < 2:
        raise ServiceError(f"cannot derive a repository from {repo_url!r}")
    return "/".join(parts[-2:])


def ci_runs_for_repo(slug: str, limit: int = 10) -> dict[str, Any]:
    """GitHub Actions runs for an arbitrary repo slug (a tenant's own app).

    Same shape and same fail-soft contract as ``ci_runs()``, but the caller
    supplies the repository instead of it being this platform's own.
    """
    fields = "databaseId,name,displayTitle,status,conclusion,workflowName,headBranch,event,createdAt,url,headSha"
    res = _run(
        ["gh", "run", "list", "--repo", slug, "--limit", str(limit), "--json", fields],
        timeout=GH_TIMEOUT,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "gh CLI unavailable", "repo": slug, "runs": []}
    try:
        runs = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse gh output", "repo": slug, "runs": []}
    return {"reachable": True, "repo": slug, "runs": runs}


# ─── GitHub Actions (real, works without cluster) ───

def ci_runs(limit: int = 15) -> dict[str, Any]:
    slug = _repo_slug()
    fields = "databaseId,name,displayTitle,status,conclusion,workflowName,headBranch,event,createdAt,url,headSha"
    res = _run(["gh", "run", "list", "--repo", slug, "--limit", str(limit), "--json", fields], timeout=GH_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "gh CLI unavailable", "runs": []}
    try:
        runs = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse gh output", "runs": []}
    return {"reachable": True, "repo": slug, "runs": runs}


def ci_trigger(workflow: str = "ci-cd.yml", ref: str | None = None) -> dict[str, Any]:
    slug = _repo_slug()
    ref = ref or _git(["rev-parse", "--abbrev-ref", "HEAD"]) or "main"
    res = _run(["gh", "workflow", "run", workflow, "--repo", slug, "--ref", ref], timeout=GH_TIMEOUT)
    if not res["ok"]:
        raise ServiceError(res["stderr"] or "failed to trigger workflow")
    return {"ok": True, "message": f"triggered '{workflow}' on {ref}"}


def ci_rerun(run_id: str) -> dict[str, Any]:
    slug = _repo_slug()
    res = _run(["gh", "run", "rerun", run_id, "--repo", slug, "--failed"], timeout=GH_TIMEOUT)
    if not res["ok"]:
        raise ServiceError(res["stderr"] or f"failed to rerun {run_id}")
    return {"ok": True, "message": f"re-running failed jobs of run {run_id}"}


def ci_cancel(run_id: str) -> dict[str, Any]:
    slug = _repo_slug()
    res = _run(["gh", "run", "cancel", run_id, "--repo", slug], timeout=GH_TIMEOUT)
    if not res["ok"]:
        raise ServiceError(res["stderr"] or f"failed to cancel {run_id}")
    return {"ok": True, "message": f"cancelled run {run_id}"}


# ─── CI pipeline graph ───

GRAPH_STATUSES = ("pending", "running", "succeeded", "failed", "skipped", "cancelled")

_GH_QUEUED_STATUSES = ("queued", "waiting", "requested", "pending")
_GH_FAILED_CONCLUSIONS = ("failure", "timed_out", "startup_failure", "stale")
_ROLLUP_PRECEDENCE = ("failed", "running", "pending", "cancelled", "succeeded", "skipped")


def _map_gh_status(gj: dict[str, Any]) -> tuple[str, str | None]:
    """GitHub job/run status + conclusion → graph vocabulary.

    Returns ``(status, detail)``; detail carries the raw conclusion only for
    the unknown → skipped case, so the renderer can say *why* a node is grey.
    """
    status = gj.get("status", "")
    conclusion = gj.get("conclusion") or ""
    if status != "completed":
        if status == "in_progress":
            return "running", None
        if status in _GH_QUEUED_STATUSES:
            return "pending", None
        # Unknown non-completed status → skipped (safe fallback)
        return "skipped", f"status: {status}" if status else None
    if conclusion == "success":
        return "succeeded", None
    if conclusion in _GH_FAILED_CONCLUSIONS:
        return "failed", None
    if conclusion == "cancelled":
        return "cancelled", None
    if conclusion == "skipped":
        return "skipped", None
    # neutral / action_required / null / unknown
    return "skipped", f"conclusion: {conclusion}" if conclusion else None


def _rollup_statuses(statuses: list[str]) -> str:
    """Matrix collapse. Precedence: failed → running → pending → cancelled
    → succeeded → skipped."""
    if not statuses:
        return "skipped"
    for s in _ROLLUP_PRECEDENCE:
        if s in statuses:
            return s
    return "skipped"


def _gh_dt(value: Any) -> datetime | None:
    """Parse a gh timestamp. gh serialises unset times as
    '0001-01-01T00:00:00Z' rather than null — filter those."""
    if not value:
        return None
    s = str(value)
    if s.startswith("0001-"):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _span_duration(start: datetime | None, end: datetime | None) -> float | None:
    """duration_s from start/end. A negative span (observed in live data for
    skipped jobs: startedAt > completedAt) becomes null, never negative."""
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    return delta if delta >= 0 else None


def _literal(j: dict[str, Any]) -> str:
    """The workflow job's literal name prefix, cut at ``${{``.

    GitHub reports the *rendered* job name: ``Terraform Validate (v1.5.7)``
    for a workflow name of ``Terraform Validate (v${{ matrix.version }})``.
    The literal is what the rendered name must start with."""
    name = j.get("name") or j.get("id") or ""
    return name.split("${{")[0].rstrip()


def _match_gh_job(
    wf_jobs: list[dict[str, Any]], gh_name: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Join a rendered GitHub job name onto a workflow job.

    1. EXACT: a workflow job whose name (or id) equals the rendered name.
       Mandatory: ``Deploy (manual)``, ``Secret Scan (Gitleaks)`` contain
       literal parentheses — a prefix rule would invent phantom matrices.
    2. LONGEST literal prefix: the workflow job whose ``${{``-cut name is
       the longest strict prefix of the rendered name → matrix leg, label =
       the remainder.
    3. Neither → (None, None): the gh job is not in the workflow file.

    Returns ``(workflow_job, matrix_label | None)``.
    """
    for j in wf_jobs:
        if (j.get("name") or j.get("id")) == gh_name:
            return j, None
    best: dict[str, Any] | None = None
    best_lit = ""
    for j in wf_jobs:
        lit = _literal(j)
        if len(lit) > len(best_lit) and gh_name.startswith(lit):
            best, best_lit = j, lit
    if best is None:
        return None, None
    label = gh_name[len(best_lit):].strip().strip("()").strip()
    return best, label or None


def _fanout_slug(label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._:@-]+", "-", label.strip()) or "leg"
    return slug.strip("-")


def _pending_ci_node(j: dict[str, Any]) -> dict[str, Any]:
    """A workflow job with no live data — pending, with its ``if:`` in the
    detail so "why is deploy grey" answers itself."""
    return {
        "id": j["id"],
        "label": j.get("name") or j["id"],
        "status": "pending",
        "depends_on": list(j.get("needs", [])),
        "started_at": None,
        "finished_at": None,
        "duration_s": None,
        "detail": (j.get("if") or "").strip(),
        "url": None,
        "fanout": [],
    }


def _mapped_ci_node(
    j: dict[str, Any], legs: list[tuple[str | None, dict[str, Any]]]
) -> dict[str, Any]:
    """One workflow job with its GitHub job(s) → contract node. Matrix legs
    roll up: precedence failed→running→pending→cancelled→succeeded→skipped;
    started = min, finished = max, duration clamped non-negative."""
    statuses: list[str] = []
    starts: list[datetime] = []
    ends: list[datetime] = []
    fanout: list[dict[str, Any]] = []
    for label, gj in legs:
        st, _detail = _map_gh_status(gj)
        statuses.append(st)
        start = _gh_dt(gj.get("startedAt"))
        end = _gh_dt(gj.get("completedAt"))
        if start:
            starts.append(start)
        if end:
            ends.append(end)
        if len(legs) >= 1:
            fanout.append(
                {
                    "id": f'{j["id"]}:{_fanout_slug(label or "") or len(fanout) + 1}',
                    "label": label or gj.get("name") or f"leg {len(fanout) + 1}",
                    "status": st,
                    "duration_s": _span_duration(start, end),
                    "url": gj.get("url"),
                }
            )
    start = min(starts) if starts else None
    end = max(ends) if ends else None
    return {
        "id": j["id"],
        "label": j.get("name") or j["id"],
        "status": _rollup_statuses(statuses),
        "depends_on": list(j.get("needs", [])),
        "started_at": start,
        "finished_at": end,
        "duration_s": _span_duration(start, end),
        "detail": f"{len(legs)} matrix legs" if len(legs) >= 1 else "",
        "url": None if len(legs) >= 1 else legs[0][1].get("url"),
        "fanout": fanout,
    }


def _join_gh_jobs(
    wf_jobs: list[dict[str, Any]], gh_jobs: list[dict[str, Any]], run_status: str
) -> list[dict[str, Any]]:
    """Workflow jobs + their matched GitHub jobs, plus orphan nodes for gh
    jobs that are not in the workflow file."""
    legs_by_wf: dict[str, list[tuple[str | None, dict[str, Any]]]] = {}
    orphans: list[dict[str, Any]] = []
    for gj in gh_jobs:
        gname = gj.get("name") or ""
        if not gname:
            continue
        wf, label = _match_gh_job(wf_jobs, gname)
        if wf is None:
            orphans.append(gj)
        else:
            legs_by_wf.setdefault(wf["id"], []).append((label, gj))

    nodes: list[dict[str, Any]] = []
    for j in wf_jobs:
        legs = legs_by_wf.get(j["id"], [])
        if not legs:
            # No GitHub counterpart: the job's `if:` excluded it, or the run
            # predates it. Pending while the run is going, skipped once done.
            status = "pending" if run_status in ("pending", "running") else "skipped"
            nodes.append({**_pending_ci_node(j), "status": status})
            continue
        nodes.append(_mapped_ci_node(j, legs))
    for gj in orphans:
        start = _gh_dt(gj.get("startedAt"))
        end = _gh_dt(gj.get("completedAt"))
        nodes.append(
            {
                "id": f"gh:{gj.get('databaseId') or len(nodes) + 1}",
                "label": gj.get("name") or f"job {gj.get('databaseId')}",
                "status": _map_gh_status(gj)[0],
                "depends_on": [],
                "started_at": start,
                "finished_at": end,
                "duration_s": _span_duration(start, end),
                "detail": "not in workflow file",
                "url": gj.get("url"),
                "fanout": [],
            }
        )
    return nodes


def ci_graph_static(ci: dict[str, Any] | None = None) -> dict[str, Any]:
    """The file-only DAG: every node pending. Never fails — parse_ci reads a
    local file. This is the shape degraded runs fall back to."""
    ci = ci or parse_ci()
    return {
        "version": "pipeline-graph/1",
        "source": "ci",
        "title": "CI/CD pipeline",
        "subtitle": "CI/CD Pipeline",
        "status": "pending",
        "url": None,
        "degraded": False,
        "degraded_reason": "",
        "generated_at": datetime.now(UTC),
        "nodes": [_pending_ci_node(j) for j in ci["jobs"]],
    }


def _degraded_ci_graph(ci: dict[str, Any], title: str, reason: str) -> dict[str, Any]:
    graph = ci_graph_static(ci)
    graph["title"] = title
    graph["degraded"] = True
    graph["degraded_reason"] = reason
    return graph


def ci_run_graph(run_id: str) -> dict[str, Any]:
    """Pipeline graph for a GitHub Actions run.

    DAG (depends_on) comes from the workflow file via ``parse_ci()`` — not
    from GitHub, which exposes neither job ids nor needs — so the topology
    is stable. Statuses come from one ``gh run view`` call, joined to
    workflow jobs by rendered name (matrix legs roll up into their workflow
    job). Degraded: when gh fails or times out, still return the complete
    static DAG at HTTP 200 with every node pending and ``degraded: true`` —
    the graph is fully renderable without gh, and the console's api() throws
    on any non-2xx, which would destroy that fallback.
    """
    slug = _repo_slug()
    ci = parse_ci()
    title = f"run {run_id}"

    res = _run(
        [
            "gh", "run", "view", run_id, "--repo", slug, "--json",
            "jobs,status,conclusion,displayTitle,headBranch,event,url,createdAt", "--",
        ],
        timeout=GH_TIMEOUT,
    )
    if not res["ok"]:
        return _degraded_ci_graph(ci, title, res["stderr"] or "gh CLI unavailable")
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return _degraded_ci_graph(ci, title, "could not parse gh run view output")

    run_status = _map_gh_status(data)[0]
    nodes = _join_gh_jobs(ci["jobs"], data.get("jobs", []), run_status)

    branch = data.get("headBranch") or ""
    event = data.get("event") or ""
    subtitle = "CI/CD Pipeline"
    if branch or event:
        subtitle += " · " + " · ".join(x for x in (branch, event) if x)

    return {
        "version": "pipeline-graph/1",
        "source": "ci",
        "title": data.get("displayTitle") or title,
        "subtitle": subtitle,
        "status": run_status,
        "url": data.get("url"),
        "degraded": False,
        "degraded_reason": "",
        "generated_at": datetime.now(UTC),
        "nodes": nodes,
    }


# ─── Kubernetes cluster reachability ───

def cluster_status() -> dict[str, Any]:
    res = _run(["kubectl", "get", "nodes", "-o", "json"], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "nodes": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output", "nodes": []}
    nodes = []
    for n in data.get("items", []):
        conds = {c["type"]: c["status"] for c in n.get("status", {}).get("conditions", [])}
        nodes.append({
            "name": n["metadata"]["name"],
            "ready": conds.get("Ready") == "True",
            "version": n.get("status", {}).get("nodeInfo", {}).get("kubeletVersion"),
        })
    return {"reachable": True, "context": _kube_context(), "nodes": nodes}


def _kube_context() -> str:
    res = _run(["kubectl", "config", "current-context"], timeout=3)
    return res["stdout"].strip() if res["ok"] else ""


def namespace_workloads(namespace: str) -> dict[str, Any]:
    """Deployments + services running in ONE namespace.

    The tenant-facing answer to "what is actually running for my app". The
    admin console's ArgoCD tab cannot answer this for a tenant: ArgoCD only
    manages this platform's own services, while tenant apps are applied with
    plain kubectl into their project's namespace. Callers must derive
    ``namespace`` from the project id server-side (``k8s_namespace``), never
    from anything the client sent, or this becomes a cross-tenant read.
    """
    dep_res = _run(
        ["kubectl", "get", "deployments", "-n", namespace, "-o", "json"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not dep_res["ok"]:
        return {"reachable": False, "error": dep_res["stderr"], "namespace": namespace,
                "deployments": [], "services": []}
    try:
        dep_data = json.loads(dep_res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output",
                "namespace": namespace, "deployments": [], "services": []}

    deployments = []
    for d in dep_data.get("items", []):
        spec, status = d.get("spec", {}), d.get("status", {})
        desired = spec.get("replicas", 0)
        ready = status.get("readyReplicas", 0) or 0
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        deployments.append({
            "name": d["metadata"]["name"],
            "desired": desired,
            "ready": ready,
            "available": status.get("availableReplicas", 0) or 0,
            "updated": status.get("updatedReplicas", 0) or 0,
            "healthy": bool(desired) and ready == desired,
            "images": [c.get("image", "") for c in containers],
            "created_at": d["metadata"].get("creationTimestamp"),
            # Provisioning installs platform-owned objects into the tenant's
            # own namespace — the per-tenant Tekton Dashboard is one, and on a
            # namespace where nothing has deployed yet it is the ONLY one. Left
            # unmarked it reads as "your app is running and healthy" to someone
            # whose deployment was in fact blocked. The label is already on the
            # object; the tenant view just has to carry it through.
            "owner": (
                "platform"
                if d["metadata"].get("labels", {}).get("app.kubernetes.io/part-of") == "devops-platform"
                else "tenant"
            ),
        })

    services = []
    svc_res = _run(
        ["kubectl", "get", "services", "-n", namespace, "-o", "json"],
        timeout=KUBECTL_TIMEOUT,
    )
    if svc_res["ok"]:
        try:
            for s in json.loads(svc_res["stdout"]).get("items", []):
                spec = s.get("spec", {})
                services.append({
                    "name": s["metadata"]["name"],
                    "type": spec.get("type", ""),
                    "cluster_ip": spec.get("clusterIP", ""),
                    "ports": [
                        {"port": p.get("port"), "target_port": p.get("targetPort"), "protocol": p.get("protocol")}
                        for p in spec.get("ports", [])
                    ],
                })
        except json.JSONDecodeError:
            pass

    return {
        "reachable": True,
        "namespace": namespace,
        "deployments": deployments,
        "services": services,
    }


def namespace_quota_usage(namespace: str) -> dict[str, Any]:
    """Used vs hard for the ResourceQuota provisioned alongside this namespace.

    The console showed only the static limit (computed client-side from the
    project's own spec) with no sense of how much of it is actually consumed
    — a tenant had no way to tell "I'm at 90% of my pod quota" from "I'm at
    5%" without running kubectl themselves. The ResourceQuota object already
    tracks both numbers server-side (`status.used`/`status.hard`); this just
    reads them instead of reinventing the accounting.
    """
    res = _run(
        ["kubectl", "get", "resourcequota", "-n", namespace, "-o", "json"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "resources": {}}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output", "resources": {}}

    resources: dict[str, dict[str, str]] = {}
    for quota in data.get("items", []):
        status = quota.get("status", {})
        hard = status.get("hard", {})
        used = status.get("used", {})
        for key in hard:
            resources[key] = {"used": used.get(key, "0"), "hard": hard[key]}
    return {"reachable": True, "resources": resources}


def namespace_pipelineruns(namespace: str) -> dict[str, Any]:
    """Tekton PipelineRuns in this tenant's own namespace, newest first.

    `_install_tenant_pipeline` installs the Pipeline/Task objects into every
    namespace-mode project at provision time regardless of TEKTON_ENABLED —
    only whether a deploy actually *submits* a run against them is gated by
    that setting. So this can be empty (no run yet, or the sandbox path is
    what's actually deploying) without meaning anything is broken; the caller
    decides how to word that, this just reports what is there.
    """
    from controlplane.core.tekton_status import condition_status

    res = _run(
        ["kubectl", "get", "pipelineruns.tekton.dev", "-n", namespace,
         "-o", "json", "--sort-by=.metadata.creationTimestamp"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "runs": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output", "runs": []}

    runs = []
    for item in reversed(data.get("items", [])):
        meta = item.get("metadata", {})
        condition = ((item.get("status") or {}).get("conditions") or [{}])
        reason = next((c.get("reason") for c in condition if c.get("type") == "Succeeded"), None)
        runs.append({
            "name": meta.get("name"),
            "status": condition_status(item),
            "reason": reason,
            "started_at": (item.get("status") or {}).get("startTime"),
            "finished_at": (item.get("status") or {}).get("completionTime"),
        })
    return {"reachable": True, "runs": runs}


def pods_status(namespace: str | None = None) -> dict[str, Any]:
    cmd = ["kubectl", "get", "pods", "-o", "json"]
    cmd += ["-n", namespace] if namespace else ["-A"]
    res = _run(cmd, timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "pods": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output", "pods": []}
    pods = []
    for p in data.get("items", []):
        st = p.get("status", {})
        ready_conds = [c for c in st.get("conditions", []) if c["type"] == "Ready"]
        cstatuses = st.get("containerStatuses", [])
        restarts = sum(cs.get("restartCount", 0) for cs in cstatuses)
        waiting = next(
            (cs["state"]["waiting"] for cs in cstatuses if "waiting" in cs.get("state", {})), None
        )
        pods.append({
            "name": p["metadata"]["name"],
            "namespace": p["metadata"]["namespace"],
            "service": p.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/name"),
            "phase": st.get("phase"),
            "ready": bool(ready_conds) and ready_conds[0]["status"] == "True",
            "restarts": restarts,
            "image": cstatuses[0].get("image") if cstatuses else None,
            "waiting_reason": (waiting or {}).get("reason"),
            "waiting_message": (waiting or {}).get("message"),
        })
    return {"reachable": True, "pods": pods}


# ─── ArgoCD — Applications are CRDs, read/sync via kubectl ───

def argocd_apps() -> dict[str, Any]:
    res = _run(["kubectl", "get", "applications.argoproj.io", "-n", "argocd", "-o", "json"], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "apps": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output", "apps": []}
    apps = []
    for a in data.get("items", []):
        st = a.get("status", {})
        apps.append({
            "name": a["metadata"]["name"],
            "sync_status": st.get("sync", {}).get("status", "Unknown"),
            "health_status": st.get("health", {}).get("status", "Unknown"),
            "revision": (st.get("sync", {}).get("revision") or "")[:7],
            "repo": a.get("spec", {}).get("source", {}).get("repoURL", ""),
            "path": a.get("spec", {}).get("source", {}).get("path", ""),
            "last_sync": st.get("operationState", {}).get("finishedAt"),
        })
    return {"reachable": True, "apps": apps}


def argocd_app_resources(app_name: str) -> dict[str, Any]:
    res = _run(["kubectl", "get", "application", app_name, "-n", "argocd", "-o", "json"], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "resources": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output", "resources": []}
    resources = [
        {
            "kind": r.get("kind"),
            "name": r.get("name"),
            "namespace": r.get("namespace"),
            "status": r.get("status"),
            "health": (r.get("health") or {}).get("status"),
        }
        for r in data.get("status", {}).get("resources", [])
    ]
    return {"reachable": True, "resources": resources}


def argocd_sync(app_name: str) -> dict[str, Any]:
    patch = json.dumps({"operation": {"sync": {"revision": "HEAD", "prune": True}}})
    res = _run(
        ["kubectl", "-n", "argocd", "patch", "application", app_name, "--type", "merge", "-p", patch],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"]:
        raise ServiceError(res["stderr"] or f"failed to sync {app_name}")
    return {"ok": True, "message": f"sync triggered for '{app_name}'"}


def argocd_admin_password() -> dict[str, Any]:
    res = _run(
        ["kubectl", "get", "secret", "argocd-initial-admin-secret", "-n", "argocd",
         "-o", "jsonpath={.data.password}"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"] or not res["stdout"]:
        return {"reachable": False, "error": res["stderr"] or "secret not found"}
    import base64
    try:
        pw = base64.b64decode(res["stdout"]).decode()
    except Exception:
        return {"reachable": False, "error": "could not decode secret"}
    return {"reachable": True, "username": "admin", "password": pw}


def argocd_refresh(app_name: str) -> dict[str, Any]:
    patch = json.dumps({"metadata": {"annotations": {"argocd.argoproj.io/refresh": "hard"}}})
    res = _run(
        ["kubectl", "-n", "argocd", "patch", "application", app_name, "--type", "merge", "-p", patch],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"]:
        raise ServiceError(res["stderr"] or f"failed to refresh {app_name}")
    return {"ok": True, "message": f"hard refresh requested for '{app_name}'"}


# ─── Vault — real seal/health status + KV listing ───

VAULT_PROXY = "/api/v1/namespaces/vault/services/vault-service:8200/proxy"


def _vault_root_token() -> str | None:
    res = _run(
        ["kubectl", "get", "secret", "vault-root-token", "-n", "vault", "-o", "jsonpath={.data.root-token}"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"] or not res["stdout"]:
        return None
    import base64
    try:
        return base64.b64decode(res["stdout"]).decode()
    except Exception:
        return None


def vault_status() -> dict[str, Any]:
    res = _run(["kubectl", "get", "--raw", f"{VAULT_PROXY}/v1/sys/seal-status"], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "vault unreachable via cluster proxy"}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse vault output"}
    return {
        "reachable": True,
        "sealed": data.get("sealed"),
        "initialized": data.get("initialized"),
        "version": data.get("version"),
        "ha_enabled": data.get("ha_enabled", False),
    }


def vault_secrets(path: str = PLATFORM_NAMESPACE) -> dict[str, Any]:
    token = _vault_root_token()
    if not token:
        return {"reachable": False, "error": "could not read vault-root-token secret", "keys": []}
    # kubectl get --raw can't send custom headers (needed for X-Vault-Token),
    # so exec the vault CLI inside the pod instead — same effective path.
    res = _run(
        ["kubectl", "exec", "-n", "vault", "deploy/vault", "--",
         "env", f"VAULT_TOKEN={token}", "vault", "kv", "list", "-format=json", f"secret/{path}"],
        timeout=10,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "cannot list secrets", "keys": []}
    try:
        keys = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse vault output", "keys": []}
    return {"reachable": True, "path": path, "keys": keys}


def vault_secret_metadata(service: str, path: str = PLATFORM_NAMESPACE) -> dict[str, Any]:
    token = _vault_root_token()
    if not token:
        return {"reachable": False, "error": "could not read vault-root-token secret"}
    res = _run(
        ["kubectl", "exec", "-n", "vault", "deploy/vault", "--",
         "env", f"VAULT_TOKEN={token}", "vault", "kv", "get", "-format=json", f"secret/{path}/{service}"],
        timeout=10,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "cannot read secret metadata"}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse vault output"}
    meta = data.get("data", {}).get("metadata", {})
    field_names = sorted((data.get("data", {}).get("data") or {}).keys())
    return {
        "reachable": True,
        "service": service,
        "version": meta.get("version"),
        "created_time": meta.get("created_time"),
        "field_names": field_names,
    }


# ─── Prometheus — queried through the k8s apiserver proxy, no port-forward needed ───

def prometheus_query(promql: str, namespace: str = MONITORING_NAMESPACE, svc: str = "prometheus-operated:9090") -> dict[str, Any]:
    raw_path = f"/api/v1/namespaces/{namespace}/services/{svc}/proxy/api/v1/query"
    res = _run(["kubectl", "get", "--raw", f"{raw_path}?query={promql}"], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "result": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse prometheus output", "result": []}
    return {"reachable": True, "result": data.get("data", {}).get("result", [])}


def alert_history(limit: int = 100) -> dict[str, Any]:
    raw_path = f"/api/v1/namespaces/monitoring/services/alert-sink:8080/proxy/alerts?limit={limit}"
    res = _run(["kubectl", "get", "--raw", raw_path], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "alerts": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse alert-sink output", "alerts": []}
    return {"reachable": True, "alerts": data.get("alerts", [])[:limit]}


# ──────────────────────────────────────────────────────────────
# Cluster drift detection — what's running vs. what git declares.
# Two signals, not one: ArgoCD's own status.resources (what it thinks
# it manages) plus a raw repo-file scan (what's declared anywhere in
# k8s/, whether or not ArgoCD applied it). Three shared objects had
# their argocd.argoproj.io/tracking-id annotation deliberately removed
# earlier this session (to stop per-service Applications fighting over
# ownership) — they land in "in-git-not-gitops" here, correctly, not as
# false "untracked" drift.
# ──────────────────────────────────────────────────────────────

DRIFT_KINDS = "deployments,statefulsets,daemonsets,services"


def repo_declared_objects() -> set[tuple[str, str]]:
    """(kind, name) for every object declared anywhere under k8s/. Pure
    file scan, no cluster access — safe to unit test without a cluster."""
    declared: set[tuple[str, str]] = set()
    k8s_dir = ROOT / "k8s"
    if not k8s_dir.is_dir():
        return declared
    for path in k8s_dir.rglob("*.yaml"):
        for doc in _load_yaml_docs(path):
            kind = doc.get("kind")
            name = (doc.get("metadata") or {}).get("name")
            if kind and name:
                declared.add((kind, name))
    return declared


def drift_report(namespaces: list[str] | None = None) -> dict[str, Any]:
    namespaces = namespaces or [PLATFORM_NAMESPACE, MONITORING_NAMESPACE]

    argo_res = _run(["kubectl", "get", "applications.argoproj.io", "-n", "argocd", "-o", "json"], timeout=KUBECTL_TIMEOUT)
    if not argo_res["ok"]:
        return {"reachable": False, "error": argo_res["stderr"], "objects": []}
    try:
        argo_data = json.loads(argo_res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output", "objects": []}
    argo_managed: set[tuple[str, str, str]] = set()
    for app in argo_data.get("items", []):
        for r in app.get("status", {}).get("resources", []):
            argo_managed.add((r.get("kind"), r.get("name"), r.get("namespace")))

    repo_declared = repo_declared_objects()

    objects = []
    for ns in namespaces:
        res = _run(["kubectl", "get", DRIFT_KINDS, "-n", ns, "-o", "json"], timeout=KUBECTL_TIMEOUT)
        if not res["ok"]:
            continue
        try:
            data = json.loads(res["stdout"])
        except json.JSONDecodeError:
            continue
        for o in data.get("items", []):
            if o.get("metadata", {}).get("ownerReferences"):
                continue  # skip controller-owned objects (RS/pods etc.)
            kind = o["kind"]
            name = o["metadata"]["name"]
            annotations = o["metadata"].get("annotations", {})
            key3 = (kind, name, ns)
            if key3 in argo_managed:
                status = "argocd"
            elif "argocd.argoproj.io/tracking-id" in annotations:
                status = "argocd-annotated"
            elif (kind, name) in repo_declared:
                status = "in-git-not-gitops"
            else:
                status = "untracked"
            objects.append({"kind": kind, "name": name, "namespace": ns, "status": status})

    counts: dict[str, int] = {}
    for o in objects:
        counts[o["status"]] = counts.get(o["status"], 0) + 1
    objects.sort(key=lambda o: {"untracked": 0, "in-git-not-gitops": 1, "argocd-annotated": 2, "argocd": 3}.get(o["status"], 9))
    return {"reachable": True, "namespaces": namespaces, "objects": objects, "counts": counts}


def _resolve_service(namespace: str, candidates: list[str], selector: str = "") -> str | None:
    """Return the first of ``candidates`` that actually exists in ``namespace``.

    The same component carries different Service names depending on how it was
    installed: the Prometheus Operator creates ``alertmanager-operated`` and
    ``prometheus-operated``, while the community Helm charts create
    ``prometheus-alertmanager`` and ``prometheus-server``. Hardcoding either
    convention makes the console report a healthy component as unreachable on
    every install that used the other one — which is exactly what happened
    here, and the message ("services ... not found") reads like an outage
    rather than a naming mismatch.

    ``selector`` is tried first when given, because a label survives a rename;
    the explicit name list is the fallback for charts that do not set the
    conventional labels.
    """
    if selector:
        res = _run(
            ["kubectl", "get", "svc", "-n", namespace, "-l", selector,
             "-o", "jsonpath={.items[*].metadata.name}"],
            timeout=KUBECTL_TIMEOUT,
        )
        if res["ok"]:
            # Prefer a non-headless Service: a headless one has no ClusterIP,
            # so the API-server proxy and port-forward both fail against it.
            found = [n for n in res["stdout"].split() if not n.endswith("-headless")]
            if found:
                return found[0]

    res = _run(
        ["kubectl", "get", "svc", "-n", namespace, "-o", "jsonpath={.items[*].metadata.name}"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"]:
        return None
    present = set(res["stdout"].split())
    for name in candidates:
        if name in present:
            return name
    return None


# Both naming conventions, most specific first. See _resolve_service.
ALERTMANAGER_SERVICES = ["alertmanager-operated", "prometheus-alertmanager", "alertmanager"]
PROMETHEUS_SERVICES = ["prometheus-operated", "prometheus-server", "prometheus"]


def alerts_firing() -> dict[str, Any]:
    service = _resolve_service(
        MONITORING_NAMESPACE, ALERTMANAGER_SERVICES, selector="app.kubernetes.io/name=alertmanager"
    )
    if not service:
        return {
            "reachable": False,
            "error": (
                "no Alertmanager Service found in namespace 'monitoring' (looked for "
                + ", ".join(ALERTMANAGER_SERVICES)
                + " and label app.kubernetes.io/name=alertmanager)"
            ),
            "alerts": [],
        }
    raw_path = f"/api/v1/namespaces/monitoring/services/{service}:9093/proxy/api/v2/alerts"
    res = _run(["kubectl", "get", "--raw", raw_path], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "alerts": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse alertmanager output", "alerts": []}
    firing = [
        {
            "name": a.get("labels", {}).get("alertname"),
            "severity": a.get("labels", {}).get("severity"),
            "service": a.get("labels", {}).get("service") or a.get("labels", {}).get("app"),
            "state": a.get("status", {}).get("state"),
            "starts_at": a.get("startsAt"),
            "labels": a.get("labels", {}),
            "annotations": a.get("annotations", {}),
            "generator_url": a.get("generatorURL"),
        }
        for a in data if isinstance(data, list)
    ]
    return {"reachable": True, "alerts": firing}


# ──────────────────────────────────────────────────────────────
# Dashboard access — the real UIs (ArgoCD, Grafana, Prometheus)
# are ClusterIP-only, not reachable from outside the cluster.
# On-demand kubectl port-forward gives a real, working local URL
# without permanently exposing anything.
# ──────────────────────────────────────────────────────────────

# No scheme is declared here: what a service speaks on its port is probed once
# the forward is up (_detect_scheme). A hardcoded wrong scheme produces an
# iframe that stays blank with nothing in the console, because the browser
# fails the connection instead of the request — Vault was declared https while
# it runs dev mode over plain HTTP (k8s/vault/manifests.yaml).
#
# ArgoCD is reached over its `http` port, not 443: 443 speaks TLS with a
# self-signed cert, and a certificate interstitial cannot be shown inside an
# iframe, so that port can never be embedded no matter what the scheme says.
# Port 80 serves the UI directly once argocd-server runs with --insecure
# (k8s/argocd/install/anonymous-access-patch.yaml); without that flag it
# 307s to https and _detect_scheme follows it back into the same dead end.
DASHBOARDS = {
    "argocd": {"namespace": "argocd", "service": "svc/argocd-server", "remote_port": 80, "label": "ArgoCD"},
    "grafana": {"namespace": MONITORING_NAMESPACE, "service": "svc/grafana", "remote_port": 3000, "label": "Grafana"},
    # Prometheus and Alertmanager are resolved at open time rather than named
    # here: the Service name depends on the install method (see
    # _resolve_service). "service" stays as the last-resort fallback.
    "prometheus": {"namespace": MONITORING_NAMESPACE, "service": "svc/prometheus-server", "remote_port": 9090, "label": "Prometheus",
                   "candidates": PROMETHEUS_SERVICES, "selector": "app.kubernetes.io/name=prometheus"},
    "alertmanager": {"namespace": MONITORING_NAMESPACE, "service": "svc/prometheus-alertmanager", "remote_port": 9093, "label": "Alertmanager",
                     "candidates": ALERTMANAGER_SERVICES, "selector": "app.kubernetes.io/name=alertmanager"},
    "vault": {"namespace": "vault", "service": "svc/vault-service", "remote_port": 8200, "label": "Vault"},
    "kibana": {"namespace": MONITORING_NAMESPACE, "service": "svc/kibana", "remote_port": 5601, "label": "Kibana"},
}


def _probe_forward(port: int) -> tuple[str, bool]:
    """Ask the forwarded port what it speaks and whether it may be framed.

    Returns ``(scheme, embeddable)``. Both answers come from the service
    itself rather than from a table here, because both are deployment
    choices that change without this file changing:

    * scheme — a plain-HTTP request to a TLS listener gets no HTTP response
      at all, so "did this answer with HTTP/" is the whole test. A redirect
      to https on the same port means the service wants TLS even though it
      accepted the plain request, which is argocd-server without --insecure.
    * embeddable — ``X-Frame-Options`` or a CSP ``frame-ancestors`` that
      excludes us. Vault always sends ``frame-ancestors 'none'`` and cannot
      be iframed at any scheme; Grafana sends nothing only when
      GF_SECURITY_ALLOW_EMBEDDING is on. An iframe refused this way renders
      as an empty grey box with no console error, so the caller has to know
      before it builds one.
    """
    import http.client

    try:
        path = "/"
        # Follow the tool's own redirects: the frame-blocking headers are on
        # the page it actually serves, not on the redirect. Vault answers "/"
        # with a bare 307 to /ui/ and only /ui/ carries frame-ancestors 'none'
        # — probing one hop deep is the difference between "embeddable" and
        # the blank box it really produces.
        for _ in range(4):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            conn.request("GET", path)
            resp = conn.getresponse()
            location = resp.getheader("Location") or ""
            xfo = (resp.getheader("X-Frame-Options") or "").lower()
            csp = (resp.getheader("Content-Security-Policy") or "").lower()
            resp.read()
            conn.close()
            if 300 <= resp.status < 400 and location.startswith("/"):
                path = location
                continue
            break
        if 300 <= resp.status < 400 and location.startswith("https://"):
            # Wants TLS; its certificate is self-signed and a cert
            # interstitial cannot render inside a frame either.
            return "https", False
        frame_ancestors = ""
        for directive in csp.split(";"):
            if directive.strip().startswith("frame-ancestors"):
                frame_ancestors = directive.strip()
        blocked = bool(xfo) or (bool(frame_ancestors) and "*" not in frame_ancestors)
        return "http", not blocked
    except Exception:
        # No HTTP answer: either a TLS listener or a service that is not up
        # yet. Either way an embed of it is a blank box.
        return "https", False


_port_forwards: dict[str, dict[str, Any]] = {}


def open_dashboard(tool: str) -> dict[str, Any]:
    cfg = DASHBOARDS.get(tool)
    if not cfg:
        raise ServiceError(f"unknown dashboard '{tool}'")

    existing = _port_forwards.get(tool)
    if existing and existing["proc"].poll() is None:
        return {
            "ok": True,
            "url": existing["url"],
            "embeddable": existing["embeddable"],
            "message": f"{cfg['label']} already forwarded",
        }

    # Resolve the Service now if this tool ships under more than one name, so
    # the forward targets what is installed rather than what we guessed.
    target = cfg["service"]
    if cfg.get("candidates"):
        resolved = _resolve_service(cfg["namespace"], cfg["candidates"], cfg.get("selector", ""))
        if resolved:
            target = f"svc/{resolved}"

    local_port = 18000 + (abs(hash(tool)) % 900)
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", cfg["namespace"], target, f"{local_port}:{cfg['remote_port']}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    import time
    time.sleep(1.5)
    if proc.poll() is not None:
        err = proc.stderr.read() if proc.stderr else ""
        raise ServiceError(f"port-forward failed: {err.strip() or 'process exited'}")

    scheme, embeddable = _probe_forward(local_port)
    url = f"{scheme}://127.0.0.1:{local_port}"
    _port_forwards[tool] = {"proc": proc, "url": url, "port": local_port, "embeddable": embeddable}
    return {
        "ok": True,
        "url": url,
        "embeddable": embeddable,
        "message": f"{cfg['label']} forwarded to {url}",
    }


def close_dashboard(tool: str) -> dict[str, Any]:
    existing = _port_forwards.pop(tool, None)
    if not existing:
        return {"ok": True, "message": f"no active forward for '{tool}'"}
    existing["proc"].terminate()
    return {"ok": True, "message": f"closed forward for '{tool}'"}


def dashboard_status() -> dict[str, Any]:
    live = {}
    for tool, fw in list(_port_forwards.items()):
        alive = fw["proc"].poll() is None
        if not alive:
            _port_forwards.pop(tool, None)
        else:
            live[tool] = fw["url"]
    return live


# ─── Pod operations — logs + restart, real kubectl ───

def _pod_app_container(namespace: str, pod: str) -> str | None:
    """Which container of `pod` is the workload, for `kubectl logs -c`.

    kubectl defaults to the first container, and every service pod here leads
    with the `vault-token-refresh` sidecar — so "view logs" showed the
    sidecar's output for every service, and nobody could see their own app.
    """
    res = _run(
        ["kubectl", "get", "pod", pod, "-n", namespace,
         "-o", "jsonpath={range .spec.containers[*]}{.name}{'\\n'}{end}"],
        timeout=10,
    )
    if not res["ok"]:
        return None
    names = [n for n in res["stdout"].splitlines() if n.strip()]
    workload = re.sub(r"-[a-z0-9]+-[a-z0-9]+$", "", pod)
    picked = _app_container([{"name": n} for n in names], workload).get("name")
    return picked


def pod_logs(namespace: str, pod: str, tail: int = 200) -> dict[str, Any]:
    cmd = ["kubectl", "logs", pod, "-n", namespace, f"--tail={tail}"]
    container = _pod_app_container(namespace, pod)
    if container:
        cmd += ["-c", container]
    res = _run(cmd, timeout=10)
    if not res["ok"] and not res["stdout"]:
        return {"reachable": False, "error": res["stderr"], "log": ""}
    return {"reachable": True, "log": res["stdout"] or res["stderr"]}


def pod_restart(namespace: str, pod: str) -> dict[str, Any]:
    res = _run(["kubectl", "delete", "pod", pod, "-n", namespace], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        raise ServiceError(res["stderr"] or f"failed to restart {pod}")
    return {"ok": True, "message": f"pod '{pod}' deleted — controller will recreate it"}


def find_pod(namespace: str, name_prefix: str) -> str | None:
    res = _run(["kubectl", "get", "pods", "-n", namespace, "-o", "name"], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return None
    for line in res["stdout"].splitlines():
        name = line.split("/")[-1]
        if name.startswith(name_prefix):
            return name
    return None


def pod_detail(namespace: str, pod: str) -> dict[str, Any]:
    res = _run(["kubectl", "get", "pod", pod, "-n", namespace, "-o", "json"], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"]}
    try:
        p = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output"}
    st = p.get("status", {})
    containers = []
    for cs in st.get("containerStatuses", []):
        state = cs.get("state", {})
        last = cs.get("lastState", {}).get("terminated")
        containers.append({
            "name": cs.get("name"),
            "image": cs.get("image"),
            "ready": cs.get("ready"),
            "restart_count": cs.get("restartCount"),
            "waiting_reason": state.get("waiting", {}).get("reason"),
            "waiting_message": state.get("waiting", {}).get("message"),
            "last_terminated_reason": (last or {}).get("reason"),
            "last_terminated_exit_code": (last or {}).get("exitCode"),
            "last_terminated_at": (last or {}).get("finishedAt"),
        })
    return {
        "reachable": True,
        "name": p["metadata"]["name"],
        "phase": st.get("phase"),
        "node": p.get("spec", {}).get("nodeName"),
        "start_time": st.get("startTime"),
        "containers": containers,
    }


def pod_events(namespace: str, name: str, limit: int = 30) -> dict[str, Any]:
    res = _run(
        ["kubectl", "get", "events", "-n", namespace,
         "--field-selector", f"involvedObject.name={name}", "-o", "json"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "events": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output", "events": []}
    events = [
        {
            "type": e.get("type"),
            "reason": e.get("reason"),
            "message": e.get("message"),
            "count": e.get("count"),
            "last_timestamp": e.get("lastTimestamp"),
        }
        for e in data.get("items", [])
    ]
    events.sort(key=lambda e: e["last_timestamp"] or "", reverse=True)
    return {"reachable": True, "events": events[:limit]}


def _parse_top_output(text: str) -> dict[str, dict[str, str]]:
    """Parses `kubectl top pods --no-headers` output (no -o json support)."""
    metrics: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            metrics[parts[0]] = {"cpu": parts[1], "memory": parts[2]}
    return metrics


def pod_metrics(namespace: str) -> dict[str, Any]:
    res = _run(["kubectl", "top", "pods", "-n", namespace, "--no-headers"], timeout=10)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "metrics-server unavailable", "metrics": {}}
    return {"reachable": True, "metrics": _parse_top_output(res["stdout"])}


def _app_container(containers: list[dict[str, Any]], workload: str) -> dict[str, Any]:
    """The container that IS the service, not one of its sidecars.

    Position is not that answer. Vault's injector prepends `vault-agent` to
    every annotated pod, so containers[0] on users-service is
    `hashicorp/vault:1.15.2` — which is what the rollout history displayed for
    all 11 revisions of every service, making them look like they were running
    Vault. The container carrying the workload's own name is; failing that,
    the first container that is not a known sidecar.
    """
    if not containers:
        return {}
    by_name = {c.get("name"): c for c in containers}
    if workload in by_name:
        return by_name[workload]
    # `vault-` covers both shapes the injector produces here: the agent
    # sidecar and the `vault-token-refresh` container the chart adds, which
    # sorts first in every service pod.
    sidecars = ("vault-", "istio-proxy", "linkerd-proxy", "envoy", "filebeat")
    for c in containers:
        if not (c.get("name") or "").startswith(sidecars):
            return c
    return containers[0]


def _container_port(namespace: str, service: str) -> int | None:
    """The port the service's own container listens on, per its Deployment."""
    res = _run(
        ["kubectl", "get", "deploy", service, "-n", namespace, "-o",
         "jsonpath={.spec.template.spec.containers[?(@.name=='" + service + "')].ports[0].containerPort}"],
        timeout=10,
    )
    if not res["ok"]:
        return None
    try:
        return int(res["stdout"].strip())
    except ValueError:
        return None


def _parse_rollout_history(text: str) -> list[dict[str, Any]]:
    """Parses `kubectl rollout history deploy/<d>` tabular output."""
    revisions = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("REVISION", "deployment.apps", "daemonset.apps", "statefulset.apps")):
            continue
        parts = line.split(None, 1)
        if parts and parts[0].isdigit():
            revisions.append({
                "revision": int(parts[0]),
                "change_cause": parts[1].strip() if len(parts) > 1 else None,
            })
    return revisions


def rollout_history(namespace: str, deployment: str) -> dict[str, Any]:
    res = _run(["kubectl", "rollout", "history", f"deploy/{deployment}", "-n", namespace], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "revisions": []}
    revisions = _parse_rollout_history(res["stdout"])
    rs_res = _run(
        ["kubectl", "get", "rs", "-n", namespace, "-l", f"app.kubernetes.io/name={deployment}", "-o", "json"],
        timeout=KUBECTL_TIMEOUT,
    )
    images_by_rev: dict[int, str] = {}
    if rs_res["ok"]:
        try:
            rs_data = json.loads(rs_res["stdout"])
            for rs in rs_data.get("items", []):
                rev = rs.get("metadata", {}).get("annotations", {}).get("deployment.kubernetes.io/revision")
                containers = rs.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
                if rev and containers:
                    images_by_rev[int(rev)] = _app_container(containers, deployment).get("image")
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    for r in revisions:
        r["image"] = images_by_rev.get(r["revision"])
    return {"reachable": True, "revisions": revisions}


_K8S_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def rollout_undo(namespace: str, deployment: str, to_revision: int | None = None) -> dict[str, Any]:
    if not _K8S_NAME_RE.match(deployment):
        raise ServiceError(f"invalid deployment name '{deployment}'")
    cmd = ["kubectl", "rollout", "undo", f"deploy/{deployment}", "-n", namespace]
    if to_revision:
        cmd.append(f"--to-revision={to_revision}")
    res = _run(cmd, timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        raise ServiceError(res["stderr"] or f"rollback failed for {deployment}")
    return {"ok": True, "message": f"rolled back '{deployment}'" + (f" to revision {to_revision}" if to_revision else "")}


def service_drilldown(service: str, namespace: str = PLATFORM_NAMESPACE) -> dict[str, Any]:
    """One aggregate call for the service detail panel: pods (by label
    selector, not name-prefix guessing), each pod's detail, recent events,
    live metrics, and deployment rollout history."""
    pods_res = _run(
        ["kubectl", "get", "pods", "-n", namespace, "-l", f"app.kubernetes.io/name={service}", "-o", "json"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not pods_res["ok"]:
        return {"reachable": False, "error": pods_res["stderr"]}
    try:
        pods_data = json.loads(pods_res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse kubectl output"}

    pod_names = [p["metadata"]["name"] for p in pods_data.get("items", [])]
    pods = [pod_detail(namespace, name) for name in pod_names]
    events = [pod_events(namespace, name, limit=10) for name in pod_names]
    metrics = pod_metrics(namespace)
    history = rollout_history(namespace, service)

    all_events = []
    for e in events:
        if e.get("reachable"):
            all_events.extend(e["events"])
    all_events.sort(key=lambda e: e["last_timestamp"] or "", reverse=True)

    return {
        "reachable": True,
        "service": service,
        "pods": pods,
        "events": all_events[:20],
        "metrics": metrics.get("metrics", {}) if metrics.get("reachable") else {},
        "rollout": history,
    }


# ─── CI log viewer ───

def ci_run_logs(run_id: str) -> dict[str, Any]:
    slug = _repo_slug()
    # --log-failed first, because on a failed run it is the part anyone opening
    # this actually wants. On a SUCCESSFUL run it prints nothing and still
    # exits 0, so gating the fallback on the exit code meant every green run
    # rendered as "(empty)" in the log viewer. Empty output is the signal.
    res = _run(["gh", "run", "view", run_id, "--repo", slug, "--log-failed"], timeout=GH_TIMEOUT)
    if not res["stdout"].strip():
        res = _run(["gh", "run", "view", run_id, "--repo", slug, "--log"], timeout=GH_TIMEOUT)
    if not res["ok"] and not res["stdout"]:
        return {"reachable": False, "error": res["stderr"], "log": ""}
    return {"reachable": True, "log": res["stdout"][-8000:]}


# ──────────────────────────────────────────────────────────────
# Ops script runner — run scripts/*.sh from the UI with streamed
# output. Allowlisted only; never accept a client-supplied path.
# Popen'd (not _run, which blocks) since these take minutes; a
# background reader thread drains stdout into a growing buffer
# the frontend polls by offset.
# ──────────────────────────────────────────────────────────────

SCRIPTS: dict[str, dict[str, Any]] = {
    "smoke-test": {
        "path": "scripts/smoke-test.sh", "args": ["--ci"],
        "label": "Smoke test (live E2E)", "destructive": False,
    },
    "validate-platform": {
        "path": "scripts/validate-platform.sh", "args": ["--ci", "--skip-incident"],
        "label": "Platform gate (7 checks)", "destructive": False,
    },
    "validate-security": {
        "path": "scripts/validate-security.sh", "args": ["--ci"],
        "label": "Security gate", "destructive": False,
    },
    "stress-hpa": {
        "path": "scripts/stress-hpa.sh", "args": ["--ci", "--no-watch"],
        "label": "HPA stress (generates real load)", "destructive": True,
    },
    # Infra control (WS-B): real VM provisioning. Both scripts run their own
    # preflight and refuse with the specific reason (disk full today) before
    # any apply. Gated in the UI by node_preflight().
    "worker-add": {
        "path": "scripts/platform-worker-add.sh", "args": [],
        "label": "Provision next worker VM (terraform apply + ansible join)",
        "destructive": True,
    },
    "worker-remove": {
        "path": "scripts/platform-worker-remove.sh", "args": [],
        "label": "Drain + remove last worker VM", "destructive": True,
    },
}

MAX_SCRIPT_LINES = 4000
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _script_reader(key: str, proc: subprocess.Popen) -> None:
    job = _jobs[key]
    try:
        for line in iter(proc.stdout.readline, ""):
            clean = _ANSI_RE.sub("", line.rstrip("\n"))
            with _jobs_lock:
                job["lines"].append(clean)
                if len(job["lines"]) > MAX_SCRIPT_LINES:
                    job["lines"] = job["lines"][-MAX_SCRIPT_LINES:]
                    job["offset_base"] += 1
    finally:
        proc.wait()
        with _jobs_lock:
            job["exit_code"] = proc.returncode
            job["finished_at"] = time.time()


def list_scripts() -> dict[str, Any]:
    out = []
    for key, cfg in SCRIPTS.items():
        job = _jobs.get(key)
        running = bool(job and job["exit_code"] is None)
        out.append({
            "key": key, "label": cfg["label"], "destructive": cfg["destructive"],
            "running": running,
            "last_exit_code": job["exit_code"] if job else None,
        })
    return {"scripts": out}


def run_script(key: str) -> dict[str, Any]:
    cfg = SCRIPTS.get(key)
    if not cfg:
        raise ServiceError(f"unknown script '{key}'")
    script_path = ROOT / cfg["path"]
    if not script_path.is_file():
        raise ServiceError(f"script not found on disk: {cfg['path']}")

    with _jobs_lock:
        existing = _jobs.get(key)
        if existing and existing["exit_code"] is None:
            raise ServiceError(f"'{key}' is already running")

        proc = subprocess.Popen(
            ["bash", str(script_path), *cfg["args"]],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
        _jobs[key] = {
            "proc": proc, "lines": [], "offset_base": 0,
            "exit_code": None, "started_at": time.time(), "finished_at": None,
        }
    threading.Thread(target=_script_reader, args=(key, proc), daemon=True).start()
    return {"ok": True, "message": f"'{cfg['label']}' started"}


def script_output(key: str, offset: int = 0) -> dict[str, Any]:
    job = _jobs.get(key)
    if not job:
        return {"reachable": True, "running": False, "lines": [], "offset": 0, "exit_code": None, "duration_s": None}
    with _jobs_lock:
        visible_offset = max(offset, job["offset_base"])
        new_lines = job["lines"][visible_offset - job["offset_base"]:]
        running = job["exit_code"] is None
        duration = (job["finished_at"] or time.time()) - job["started_at"]
        return {
            "reachable": True, "running": running, "lines": new_lines,
            "offset": job["offset_base"] + len(job["lines"]),
            "exit_code": job["exit_code"], "duration_s": round(duration, 1),
        }


def stop_script(key: str) -> dict[str, Any]:
    job = _jobs.get(key)
    if not job or job["exit_code"] is not None:
        return {"ok": True, "message": f"'{key}' is not running"}
    job["proc"].terminate()
    return {"ok": True, "message": f"stop requested for '{key}'"}


# ──────────────────────────────────────────────────────────────
# Logs — Elasticsearch via kubectl exec (kubectl get --raw can't
# send an Authorization header, same constraint as _vault_root_token
# above). Filebeat ships straight to ES (no Logstash in the path,
# confirmed by its own DaemonSet config) into the filebeat-* index —
# NOT devops-platform-* as the index naming might suggest. Loki is
# also deployed but has no streams (promtail misconfigured) — both
# facts are surfaced honestly via log_pipeline_health() rather than
# assumed.
# ──────────────────────────────────────────────────────────────

def _es_credentials() -> str | None:
    res = _run(
        ["kubectl", "get", "secret", "elasticsearch-credentials", "-n", MONITORING_NAMESPACE,
         "-o", "jsonpath={.data.ELASTIC_PASSWORD}"],
        timeout=KUBECTL_TIMEOUT,
    )
    if not res["ok"] or not res["stdout"]:
        return None
    import base64
    try:
        return base64.b64decode(res["stdout"]).decode()
    except Exception:
        return None


def es_status() -> dict[str, Any]:
    pw = _es_credentials()
    if not pw:
        return {"reachable": False, "error": "could not read elasticsearch-credentials secret"}
    res = _run(
        ["kubectl", "exec", "-n", MONITORING_NAMESPACE, "elasticsearch-0", "--",
         "curl", "-s", "-u", f"elastic:{pw}", "http://localhost:9200/_cat/indices/filebeat-*?format=json"],
        timeout=15,
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "elasticsearch unreachable"}
    try:
        indices = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse elasticsearch output"}
    doc_count = sum(int(i.get("docs.count") or 0) for i in indices)
    return {"reachable": True, "indices": [i.get("index") for i in indices], "doc_count": doc_count}


def es_search_logs(service: str, query: str = "", limit: int = 100, since: str = "now-1h") -> dict[str, Any]:
    pw = _es_credentials()
    if not pw:
        return {"reachable": False, "error": "could not read elasticsearch-credentials secret", "lines": []}
    must = [{"range": {"@timestamp": {"gte": since}}}]
    if service:
        must.append({"match_phrase": {"kubernetes.container.name": service}})
    if query:
        must.append({"match": {"message": query}})
    body = {
        "size": limit,
        "sort": [{"@timestamp": "desc"}],
        "query": {"bool": {"must": must}},
    }
    res = _run(
        ["kubectl", "exec", "-i", "-n", MONITORING_NAMESPACE, "elasticsearch-0", "--",
         "curl", "-s", "-u", f"elastic:{pw}", "-H", "Content-Type: application/json",
         "-X", "POST", "http://localhost:9200/filebeat-*/_search", "-d", "@-"],
        timeout=15, input_=json.dumps(body),
    )
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"] or "elasticsearch query failed", "lines": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse elasticsearch output", "lines": []}
    hits = data.get("hits", {}).get("hits", [])
    lines = []
    for h in hits:
        src = h.get("_source", {})
        pod = src.get("kubernetes", {}).get("pod", {}).get("name", "")
        ts = src.get("@timestamp", "")
        msg = str(src.get("message", "")).strip()
        lines.append(f"{ts}  {pod}  {msg}")
    total = data.get("hits", {}).get("total", {})
    result = {
        "reachable": True,
        "hits": total.get("value", len(hits)) if isinstance(total, dict) else total,
        "lines": lines,
        "since": since,
    }
    if not lines:
        # An empty window and an empty index look identical on screen. Say
        # which one it is: the shipper can be down with the index still
        # holding days of history, which is exactly the state that made a
        # working search look broken.
        result["newest"] = _es_newest_timestamp(pw)
    return result


def _es_newest_timestamp(pw: str) -> str | None:
    res = _run(
        ["kubectl", "exec", "-i", "-n", MONITORING_NAMESPACE, "elasticsearch-0", "--",
         "curl", "-s", "-u", f"elastic:{pw}", "-H", "Content-Type: application/json",
         "-X", "POST", "http://localhost:9200/filebeat-*/_search", "-d", "@-"],
        timeout=15,
        input_=json.dumps({"size": 1, "sort": [{"@timestamp": "desc"}], "_source": ["@timestamp"]}),
    )
    if not res["ok"]:
        return None
    try:
        hits = json.loads(res["stdout"]).get("hits", {}).get("hits", [])
    except json.JSONDecodeError:
        return None
    return hits[0].get("_source", {}).get("@timestamp") if hits else None


def log_pipeline_health() -> dict[str, Any]:
    links = []

    fb = _run(["kubectl", "get", "ds", "filebeat", "-n", MONITORING_NAMESPACE, "-o", "json"], timeout=KUBECTL_TIMEOUT)
    if fb["ok"]:
        try:
            d = json.loads(fb["stdout"])
            ready = d.get("status", {}).get("numberReady", 0)
            desired = d.get("status", {}).get("desiredNumberScheduled", 0)
            links.append({"name": "filebeat", "ok": ready == desired and desired > 0, "detail": f"{ready}/{desired} ready"})
        except json.JSONDecodeError:
            links.append({"name": "filebeat", "ok": False, "detail": "could not parse status"})
    else:
        links.append({"name": "filebeat", "ok": False, "detail": "not found"})

    ls = _run(["kubectl", "get", "deploy", "logstash", "-n", MONITORING_NAMESPACE], timeout=KUBECTL_TIMEOUT)
    links.append({"name": "logstash", "ok": ls["ok"], "detail": "not deployed — filebeat ships straight to elasticsearch" if not ls["ok"] else "deployed"})

    es = es_status()
    links.append({
        "name": "elasticsearch",
        "ok": es.get("reachable") and es.get("doc_count", 0) > 0,
        "detail": f"{es.get('doc_count', 0):,} log docs in filebeat-*" if es.get("reachable") else es.get("error", "unreachable"),
    })

    loki = _run(["kubectl", "get", "--raw", "/api/v1/namespaces/monitoring/services/loki:3100/proxy/ready"], timeout=KUBECTL_TIMEOUT)
    labels = _run(["kubectl", "get", "--raw", "/api/v1/namespaces/monitoring/services/loki:3100/proxy/loki/api/v1/labels"], timeout=KUBECTL_TIMEOUT)
    has_streams = False
    if labels["ok"]:
        try:
            has_streams = bool(json.loads(labels["stdout"]).get("data"))
        except json.JSONDecodeError:
            pass
    links.append({
        "name": "loki",
        "ok": loki["ok"] and has_streams,
        "detail": "ready but no log streams (promtail misconfigured)" if loki["ok"] and not has_streams else ("ready" if loki["ok"] else "unreachable"),
    })

    return {"reachable": True, "links": links}
