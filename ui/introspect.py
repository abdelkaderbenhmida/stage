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
    gen = (apps.get("spec", {}).get("generators") or [{}])[0]
    git_gen = gen.get("git", {}) if isinstance(gen, dict) else {}
    files = git_gen.get("files") or []
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
        "files_pattern": files,
        "app_name_template": tmpl.get("metadata", {}).get("name"),
        "scoped_to": "1 Application per app/*/main.py (flat) or app/*/*/main.py (nested)",
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
