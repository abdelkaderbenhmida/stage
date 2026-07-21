# DevOps Central Platform

Plateforme DevOps de bout en bout autour de **3 microservices FastAPI** (Users, Products, Orders). Le projet démontre l'ensemble de la chaîne DevOps moderne : provisionnement IaC, configuration automatisée, conteneurisation, orchestration Kubernetes, sécurité du pipeline (DevSecOps), GitOps, et observabilité complète (métriques + logs centralisés).

> Documentation de spécification complète :
> - [`docs/DevOps_Central_Platform_Description.md`](docs/DevOps_Central_Platform_Description.md)
> - [`docs/DevOps_Central_Platform_Etapes_Implementation.md`](docs/DevOps_Central_Platform_Etapes_Implementation.md)

---

## Stack technique

| Couche | Outil |
|---|---|
| Infrastructure as Code | Terraform (provider libvirt/KVM homelab ou équivalent cloud) |
| Configuration | Ansible (rôles `docker`, `k8s_common`, `k8s_master`, `k8s_worker`) |
| Conteneurisation | Docker multi-stage, images non-root + HEALTHCHECK |
| Orchestration | Kubernetes + Helm (charts Prometheus, Grafana, ELK) |
| Sécurité | Trivy (image), Gitleaks (code), HashiCorp Vault (secrets dynamiques) |
| GitOps | GitHub Actions (CI), ArgoCD (sync), Flagger + Istio (Canary releases) |
| Observabilité | Prometheus (métriques 15s), Grafana (3 dashboards), ELK Stack (logs), AlertManager (SLO rules) |

---

## Structure du dépôt

```
devops-central-platform/
├── terraform/                # Phase 1 — IaC (main, variables, outputs, backend, inventory.tpl)
├── ansible/                  # Phase 2 — rôles docker + k8s_*
├── app/                      # Phase 3 — 3 microservices FastAPI
│   ├── users-service/  products-service/  orders-service/
│   └── shared/               # lib partagée (vault_client, log_config)
├── k8s/
│   ├── apps/                 # Deployments + Service + HPA + RBAC
│   ├── monitoring/           # Phase 6 — prometheus, grafana, elk, alertmanager
│   ├── argocd/applications/  # Phase 5 — Applications CRDs
│   ├── vault/                # Phase 4 — manifests + policy.hcl + values.yaml
│   ├── canary/               # Phase 5 — Flagger Canary + istio-gateway
│   └── istio-flagger/        # Istio + Flagger install manifests
├── .github/workflows/ci-cd.yml  # lint → gitleaks → tests → build → trivy → deploy
├── scripts/
│   ├── validate-platform.sh     # Phase 7 — 7 checks + self-heal + rollback + 7/7 PASS
│   ├── validate-security.sh     # 4 security checks (gitleaks/trivy/vault/token)
│   ├── bootstrap-vault-secret.sh
│   ├── bootstrap-elasticsearch-secret.sh
│   └── generate-inventory.sh   # Regénère terraform/inventory.ini
├── docs/                     # Spécifications source
├── .gitleaks.toml
└── README.md
```

---

## Démarrage rapide

### Prérequis
- `git`, `terraform`, `ansible`, `docker`, `kubectl`, `helm`, `jq`
- 8 Go RAM + 4 vCPU (interne libvirt/KVM) ou compte cloud AWS/GCP/Azure

### Build des images (contexte `app/`)
```bash
for svc in users-service products-service orders-service; do
  docker build -t "${svc}:1.0.0" -f "app/${svc}/Dockerfile" app/
done
```

### Provisionnement terraform → ansible → k8s
```bash
cd terraform && terraform init && terraform apply
scripts/generate-inventory.sh
cd ../ansible && ansible-playbook playbook.yml
```

### Déploiement de la plateforme sur le cluster
```bash
kubectl apply -f k8s/apps/base/namespace.yaml
kubectl apply -f k8s/vault/manifests.yaml
scripts/bootstrap-vault-secret.sh
kubectl apply -f k8s/apps/
# ArgoCD bootstrap (manual once)
kubectl apply -k k8s/argocd/install/
# ArgoCD then syncs the rest of the apps automatically
```

### Validation complète (Phase 7)
```bash
scripts/validate-platform.sh                 # summary
scripts/validate-platform.sh --ci            # gating (exit 1 on failure)
scripts/validate-platform.sh --skip-incident # skip destructive self-heal/rollback
```

---

## SLOs de référence

| SLI | SLO |
|---|---|
| Disponibilité | ≥ 99.9% sur 30 jours |
| Latence P95 | < 200 ms |
| Taux d'erreur 5xx | < 1% |
| MTTD | < 2 min |
| MTTR | < 30 min |

Règles implémentées dans [`k8s/monitoring/alertmanager/rules.yaml`](k8s/monitoring/alertmanager/rules.yaml).

---

## Pipeline CI/CD

`.github/workflows/ci-cd.yml` :

```
lint → gitleaks → test → build → trivy-scan → deploy (workflow_dispatch + protected `production` env)
```

Trivy : `--severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed` + SBOM SPDX + SARIF.

Pipeline durci : `fail-fast: false`, `concurrency: cancel-in-progress`, permissions `contents: read` par défaut + escalation par job, `paths:` filters, `workflow_dispatch` only pour le stage prod.
