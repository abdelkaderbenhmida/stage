#!/usr/bin/env python3
"""Applique le cadre des figures du rapport a toutes les captures d'interfaces web."""
import os, subprocess, sys

FRAME = os.path.join(os.path.dirname(__file__), "frame.py")

FIGURES = {
 # ---------- ArgoCD ----------
 "argocd-app-detail":      ("ArgoCD — users-service", "arbre de ressources · Synced / Healthy"),
 "argocd-resource-tree":   ("ArgoCD — arbre de ressources", "Deployment, Service, HPA, PDB, ReplicaSets, Pods"),
 # ---------- Grafana ----------
 "grafana-infra-overview": ("Grafana — Infrastructure Overview", "nœuds, pods, CPU et mémoire du cluster"),
 "grafana-app-perf":       ("Grafana — Application Performance", "RPS par endpoint, latences p95 et p99"),
 "grafana-error-rate":     ("Grafana — Error Rate SLO", "taux 5xx et seuil SLO à 1 %"),
 "slo-grafana-threshold":  ("Grafana — Error Rate SLO", "seuil 1 % tracé sur la courbe réelle"),
 "grafana-infra-detail-oom": ("Grafana — Infrastructure Detail", "ratio mémoire par conteneur et évictions"),
 "instrumentator-grafana-app": ("Grafana — Application Performance", "métriques exposées par l'instrumentateur FastAPI"),
 "grafana-under-load":     ("Grafana — Application Performance", "pendant le test de charge"),
 "scenario4-02-perf-stable": ("Grafana — Application Performance", "latence stable pendant la montée en charge"),
 # ---------- Prometheus / AlertManager ----------
 "prom-targets-up":        ("Prometheus — Targets", "serviceMonitor devops-platform-apps · 10/10 up"),
 "kubelet-scrape-targets": ("Prometheus — Targets", "collecte kubelet et cAdvisor"),
 "prom-promql-rps":        ("Prometheus — Console PromQL", "sum(rate(http_requests_total[5m])) by (service)"),
 "promql-cheatsheet-run":  ("Prometheus — Console PromQL", "latence p95 par service"),
 "slo-recording-query":    ("Prometheus — Console PromQL", "disponibilité des services sur 30 jours"),
 "am-ui-groups":           ("AlertManager — Alertes actives", "regroupement par nom d'alerte"),
 "am-slo-breach":          ("Prometheus — Règles d'alerte", "état des règles SLO et saturation"),
 "scenario3-02-alerts":    ("AlertManager — Incident mémoire", "ContainerMemoryRatioHigh et PodEvictionOngoing"),
 # ---------- Kibana ----------
 "kibana-discover":        ("Kibana — Discover", "index devops-platform-* sur la dernière heure"),
 "kibana-dataview":        ("Kibana — Data views", "création de la vue devops-platform-*"),
 "kibana-search-vault":    ("Kibana — Discover", "logs JSON applicatifs filtrés sur app.name"),
 # ---------- Application ----------
 "fastapi-swagger":        ("Swagger UI — users-service", "OpenAPI 3.1 généré par FastAPI"),
 # ---------- GitHub Actions ----------
 "ci-actions-list":        ("GitHub Actions — Workflow runs", "historique du pipeline CI/CD"),
 "ci-graph":               ("GitHub Actions — Vue graphe", "dépendances entre les jobs du pipeline"),
 "ci-timing":              ("GitHub Actions — Run time", "durée de chaque job"),
 "ci-lint-job":            ("GitHub Actions — Job Lint", "ruff, terraform fmt, yamllint, kubeconform"),
 "ci-test-pipaudit":       ("GitHub Actions — Tests + Dependency Audit", "pytest et pip-audit"),
 "ci-build-matrix":        ("GitHub Actions — Build & Push", "construction et publication de l'image users-service"),
 "ci-trivy-pass":          ("GitHub Actions — Container Scan (Trivy)", "analyse de vulnérabilités et SBOM"),
 "ci-ghcr-packages":       ("GHCR — Packages", "images publiées par le pipeline"),
 "ci-deploy-digest-step":  ("GitHub Actions — Épinglage du tag", "écriture du tag immuable dans service.yaml"),
 "gitleaks-ci-job":        ("GitHub Actions — Secret Scan", "Gitleaks sur l'ensemble du dépôt"),
 # ---------- Scenarios ----------
 "scenario1-01-push-run":  ("Scénario 1 — Du commit au déploiement", "run déclenché par le push"),
 "scenario1-02-jobs-green": ("Scénario 1 — Pipeline complet", "tous les jobs au vert en 6 min 45 s"),
 "scenario5-01-error-rate": ("Scénario 5 — Régression poussée", "pipeline rouge : la régression est bloquée"),
 "scenario5-02-alert":     ("Scénario 5 — Job en échec", "les tests unitaires détectent la régression"),
 "scenario7-01-nightly-red": ("Scénario 7 — Run nocturne", "Gitleaks détecte un secret dans l'historique"),
 "gitleaks-detection":     ("Gitleaks — Détection positive", "secret trouvé lors du scan planifié"),
}

noms = sys.argv[1:] or list(FIGURES)
for n in noms:
    f = "images/%s.png" % n
    if not os.path.exists(f):
        print("absent  %s" % f); continue
    titre, sous = FIGURES[n]
    subprocess.run([sys.executable, FRAME, f, n, titre, sous], check=True)
