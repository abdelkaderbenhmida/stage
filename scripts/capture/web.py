#!/usr/bin/env python3
"""Captures des interfaces web (ArgoCD, Grafana, Prometheus, AlertManager, Kibana, Swagger).

Prerequis : scripts/capture/ui-up.sh (port-forwards + proxy d'authentification).
Usage     : python3 scripts/capture/web.py [motif ...]
"""
import os, shutil, subprocess, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
IMAGES = os.path.join(ROOT, "images")
WORK = os.path.join(os.path.expanduser("~"), "capshots")
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")

ARGO, GRAF, KIB = "http://127.0.0.1:8803", "http://127.0.0.1:8801", "http://127.0.0.1:8802"
PROM, AM, APP = "http://127.0.0.1:9090", "http://127.0.0.1:9093", "http://127.0.0.1:18080"

def g(uid, extra=""):   # tableau de bord Grafana en mode kiosque, theme clair
    return "%s/d/%s/?kiosk&theme=light&from=now-3h&to=now%s" % (GRAF, uid, extra)

def p(expr, tab=0, rng="1h"):
    from urllib.parse import quote
    return "%s/graph?g0.expr=%s&g0.tab=%d&g0.range_input=%s&g0.stacked=0" % (PROM, quote(expr), tab, rng)

SHOTS = {
# ---------- ArgoCD ----------
"argocd-apps-grid":     (ARGO + "/applications?view=tiles", 1700, 1150, 14),
"argocd-app-detail":    (ARGO + "/applications/argocd/users-service", 1700, 1050, 16),
"argocd-resource-tree": (ARGO + "/applications/argocd/prometheus", 1700, 1050, 16),
"argocd-events":        (ARGO + "/applications/argocd/users-service?tab=events", 1700, 1050, 16),

# ---------- Prometheus ----------
"prom-targets-up":        (PROM + "/targets", 1700, 1150, 12),
"kubelet-scrape-targets": (PROM + "/targets?search=kubelet", 1700, 900, 12),
"prom-promql-rps":        (p("sum(rate(http_requests_total[5m])) by (handler)"), 1700, 900, 14),
"promql-cheatsheet-run":  (p("histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))"), 1700, 900, 14),
"slo-recording-query":    (p("avg_over_time(up{job=~\".*service.*\"}[30d])", tab=1), 1700, 750, 14),

# ---------- AlertManager ----------
"am-ui-groups": (AM + "/#/alerts", 1700, 900, 12),

# ---------- Grafana ----------
"grafana-infra-overview": (g("devops-platform-infra"), 1700, 1150, 18),
"grafana-app-perf":       (g("devops-platform-app-perf"), 1700, 1150, 18),
"grafana-error-rate":     (g("devops-platform-error-rate"), 1700, 950, 18),
"slo-grafana-threshold":  (g("devops-platform-error-rate", "&viewPanel=1"), 1700, 850, 18),
"grafana-infra-detail-oom": (g("devops-platform-infra-detail"), 1700, 1150, 18),
"instrumentator-grafana-app": (g("devops-platform-app-perf", "&from=now-1h"), 1700, 1150, 18),

# ---------- application ----------
"fastapi-swagger": (APP + "/docs", 1500, 1000, 10),
}

def shot(name, url, w, h, wait):
    html = None
    png = os.path.join(WORK, name + ".png")
    os.makedirs(WORK, exist_ok=True)
    subprocess.run([CHROMIUM, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                    "--force-device-scale-factor=2",
                    "--timeout=%d" % (wait * 1000),
                    "--window-size=%d,%d" % (w, h), "--screenshot=" + png, url],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    if not os.path.exists(png):
        print("ECHEC %s" % name); return False
    shutil.move(png, os.path.join(IMAGES, name + ".png"))
    print("OK images/%s.png" % name)
    return True

pats = sys.argv[1:]
ok = ko = 0
for n, (url, w, h, wait) in SHOTS.items():
    if pats and not any(x in n for x in pats): continue
    ok, ko = (ok + 1, ko) if shot(n, url, w, h, wait) else (ok, ko + 1)
print("\n%d captures web, %d echecs" % (ok, ko))
