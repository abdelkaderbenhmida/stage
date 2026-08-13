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
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
SERVICE_MARKER = "main.py"
DEFAULT_APP = "default"
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")


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
        for k, v in raw.items():
            if isinstance(v, str) and "fromJSON(" in v:
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
    directories: list[dict[str, Any]] = []
    for g in git_gens:
        directories.extend(g.get("directories") or [])
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
    return {
        "name": (apps.get("metadata") or {}).get("name"),
        "namespace": (apps.get("metadata") or {}).get("namespace"),
        "generator_type": "git" if git_gen else "unknown",
        "repo_url": git_gen.get("repoURL"),
        "revision": git_gen.get("revision"),
        "files_pattern": [d for d in directories if not d.get("exclude")],
        "app_name_template": tmpl.get("metadata", {}).get("name"),
        "scoped_to": "1 Application per app/* (flat) or app/*/* (nested), by directory presence",
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
    return {"ok": True, "message": f"app '{name}' and its services deleted (cascade)"}


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
    return {
        "ok": True,
        "message": f"service '{k8s}' deleted — CI matrix shrinks, ArgoCD prunes, Vault role idles",
    }


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
    layer_ok = base["layer_checks"]
    base["status"] = "ready" if all(layer_ok.values()) else "partial"
    return base


# ──────────────────────────────────────────────────────────────
# LIVE STATUS + ACTIONS — real calls against GitHub, the cluster,
# ArgoCD (as K8s CRDs) and Vault. Every function fails soft: on
# timeout/unreachable it returns {"reachable": False, "error": ...}
# instead of raising, so the UI can render an honest offline state.
# ──────────────────────────────────────────────────────────────

KUBECTL_TIMEOUT = 6
GH_TIMEOUT = 25


def _run(cmd: list[str], timeout: int = KUBECTL_TIMEOUT, input_: str | None = None) -> dict[str, Any]:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), input=input_,
        )
        return {"ok": out.returncode == 0, "stdout": out.stdout, "stderr": out.stderr.strip()}
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": f"{cmd[0]} not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"{cmd[0]} timed out after {timeout}s"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "stdout": "", "stderr": str(exc)}


def _repo_slug() -> str:
    url = _git(["remote", "get-url", "origin"])
    url = url.removesuffix(".git")
    if url.startswith("git@"):
        return url.split(":", 1)[-1]
    return "/".join(url.split("/")[-2:])


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
        restarts = sum(cs.get("restartCount", 0) for cs in st.get("containerStatuses", []))
        pods.append({
            "name": p["metadata"]["name"],
            "namespace": p["metadata"]["namespace"],
            "phase": st.get("phase"),
            "ready": bool(ready_conds) and ready_conds[0]["status"] == "True",
            "restarts": restarts,
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


def vault_secrets(path: str = "devops-platform") -> dict[str, Any]:
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


# ─── Prometheus — queried through the k8s apiserver proxy, no port-forward needed ───

def prometheus_query(promql: str, namespace: str = "monitoring", svc: str = "prometheus-operated:9090") -> dict[str, Any]:
    raw_path = f"/api/v1/namespaces/{namespace}/services/{svc}/proxy/api/v1/query"
    res = _run(["kubectl", "get", "--raw", f"{raw_path}?query={promql}"], timeout=KUBECTL_TIMEOUT)
    if not res["ok"]:
        return {"reachable": False, "error": res["stderr"], "result": []}
    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return {"reachable": False, "error": "could not parse prometheus output", "result": []}
    return {"reachable": True, "result": data.get("data", {}).get("result", [])}


def alerts_firing() -> dict[str, Any]:
    raw_path = "/api/v1/namespaces/monitoring/services/alertmanager-operated:9093/proxy/api/v2/alerts"
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

DASHBOARDS = {
    "argocd": {"namespace": "argocd", "service": "svc/argocd-server", "remote_port": 443, "scheme": "https", "label": "ArgoCD"},
    "grafana": {"namespace": "monitoring", "service": "svc/grafana", "remote_port": 3000, "scheme": "http", "label": "Grafana"},
    "prometheus": {"namespace": "monitoring", "service": "svc/prometheus", "remote_port": 9090, "scheme": "http", "label": "Prometheus"},
    "alertmanager": {"namespace": "monitoring", "service": "svc/alertmanager", "remote_port": 9093, "scheme": "http", "label": "Alertmanager"},
    "vault": {"namespace": "vault", "service": "svc/vault-service", "remote_port": 8200, "scheme": "https", "label": "Vault"},
}

_port_forwards: dict[str, dict[str, Any]] = {}


def open_dashboard(tool: str) -> dict[str, Any]:
    cfg = DASHBOARDS.get(tool)
    if not cfg:
        raise ServiceError(f"unknown dashboard '{tool}'")

    existing = _port_forwards.get(tool)
    if existing and existing["proc"].poll() is None:
        return {"ok": True, "url": existing["url"], "message": f"{cfg['label']} already forwarded"}

    local_port = 18000 + (abs(hash(tool)) % 900)
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", cfg["namespace"], cfg["service"], f"{local_port}:{cfg['remote_port']}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    import time
    time.sleep(1.5)
    if proc.poll() is not None:
        err = proc.stderr.read() if proc.stderr else ""
        raise ServiceError(f"port-forward failed: {err.strip() or 'process exited'}")

    url = f"{cfg['scheme']}://127.0.0.1:{local_port}"
    _port_forwards[tool] = {"proc": proc, "url": url, "port": local_port}
    return {"ok": True, "url": url, "message": f"{cfg['label']} forwarded to {url}"}


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

def pod_logs(namespace: str, pod: str, tail: int = 200) -> dict[str, Any]:
    res = _run(["kubectl", "logs", pod, "-n", namespace, f"--tail={tail}"], timeout=10)
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


# ─── CI log viewer ───

def ci_run_logs(run_id: str) -> dict[str, Any]:
    slug = _repo_slug()
    res = _run(["gh", "run", "view", run_id, "--repo", slug, "--log-failed"], timeout=GH_TIMEOUT)
    if not res["stdout"] and not res["ok"]:
        res = _run(["gh", "run", "view", run_id, "--repo", slug, "--log"], timeout=GH_TIMEOUT)
    if not res["ok"] and not res["stdout"]:
        return {"reachable": False, "error": res["stderr"], "log": ""}
    return {"reachable": True, "log": res["stdout"][-8000:]}
