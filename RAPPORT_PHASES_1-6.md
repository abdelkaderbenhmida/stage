# DevOps Central Platform — Rapport complet des phases (1 à 6)

**Date:** 2026-07-16
**Branche:** `clean-main` (historique propre, 5 commits)
**Branche originale:** `main` (54 commits sales)
**Repo:** https://github.com/abdelkaderbenhmida/stage

---

## Résumé exécutif

Ce rapport documente l'ensemble du travail réalisé sur la plateforme DevOps Central,
de l'infrastructure Terraform à l'observabilité complète. Chaque phase est un commit
atomique contenant l'intégralité des changements de cette phase.

Problèmes identifiés (P0/P1/P2) et corrections intégrées dans chaque phase.

---

## Phase 1&2 — Infrastructure Terraform + Cluster Kubernetes avec Ansible

**Commit:** `0c95ad6` — feat(phase1&2): infrastructure Terraform + cluster K8s with Ansible
**Fichiers:** 41 | **Insertions:** 5 538

### Objectif
Provisionner 3 serveurs libvirt (1 master + 2 workers) et installer Kubernetes.

### Ce qui a été fait

#### Terraform (Phase 1)
- `terraform/main.tf` — ressources VMs libvirt (1 master, 2 workers), network privé 192.168.56.0/24
- `terraform/variables.tf` — variables paramétrables (network_cidr, worker_count, vm_vcpu, vm_ram, ssh_public_key)
- `terraform/outputs.tf` — expose IPs des nœuds créés (5 outputs)
- `terraform/backend.tf` — backend S3 documenté pour migration future
- `terraform/inventory.tpl` — template générant automatiquement l'inventaire Ansible
- `terraform/cloud-init.tpl` — cloud-init pour durcir serveurs
- `terraform/network-config.tpl` — configuration réseau privée

#### Ansible (Phase 2)
- `ansible/playbook.yml` — playbook complet bootstrap cluster
- Rôle **docker** — installation Docker CE, activation service, ajout user
- Rôle **k8s_common** — swapoff, modules br_netfilter/overlay, kubeadm/kubelet/kubectl
- Rôle **k8s_master** — `kubeadm init`, Calico CNI, token join
- Rôle **k8s_worker** — exécution commande join sur chaque worker
- Rôle **k8s_reset** — reset gated par `reset_confirmed=true` + tag `never`
- `ansible/requirements.yml` — déclare collection `community.general`

### Problèmes corrigés
- **P0 #4:** tfstate + fichiers inventory retirés du tracking git. `.gitignore` bloque `*.tfstate*`
- **P1:** `ssh_public_key` marqué `sensitive = true`. Tous les outputs marqués sensitive
- **P1:** `validate-security.sh` non existant à ce stade (créé Phase 4)
- **P2:** `kubeadm` join txt `0644` → `0600` (sécurité)
- **P2:** Cloud-init durci: `ssh_pwauth: false`, `disable_root`, fail2ban, sudoers restreint
- **P2:** Terraform `validation` blocks sur `network_cidr`/`worker_count`/`vm_vcpu`
- **P2:** `gateway_ip` dérivé via `cidrhost(var.network_cidr, 1)` (pas codé en dur)
- **P2:** `dns` forwarders branchés sur `var.dns_servers`
- **P2:** TF lock constraint `~> 0.7` corrigé en `~> 0.9`
- **P2:** UID/GID `64055/993` → `var.libvirt_volume_owner_uid/gid`
- **P2:** rôles Ansible: `defaults/main.yml` + `meta/main.yml` (Galaxy-compliant)

### Critère de validation
```bash
kubectl get nodes    # 3 nœuds Ready
terraform output     # 3 IPs affichées
```

---

## Phase 3 — Conteneurisation des microservices FastAPI

**Commit:** `a944083` — feat(phase3): conteneurisation microservices FastAPI
**Fichiers:** 17 | **Insertions:** 612

### Objectif
Empaqueter 3 microservices FastAPI (Users, Products, Orders) en images Docker
prêtes à déployer sur Kubernetes.

### Ce qui a été fait
- `app/users-service/main.py` — service FastAPI avec routes API, `/health`, `/metrics`
- `app/products-service/main.py` — idem (product list, health, metrics)
- `app/orders-service/main.py` — idem (order list, health, metrics)
- `app/*/Dockerfile` — multi-stage: builder pip → final slim (python:3.12-slim)
- `k8s/apps/*-deployment.yaml` — Deployment + Service (2 replicas, port 8000, probes)
- `k8s/apps/rbac.yaml` — ServiceAccount, RBAC par service
- `k8s/apps/hpa.yaml` — HorizontalPodAutoscaler

### Problèmes identifiés et corrigés (plus tard)
- `/health` initial = endpoint unique (splité en `/livez` + `/readyz` en Phase 5)
- `prometheus_client.Counter` manuel dans chaque handler (remplacé par Instrumentator en Phase 6)
- `resources.requests/limits` définis mais LimitRange trop basse (corrigé Phase 5)
- `readOnlyRootFilesystem` + `seccompProfile` + `drop ALL` ajoutés Phase 5 (P2)

### Critère de validation
```bash
kubectl get pods -n devops-platform    # tous Running, probes OK
```

---

## Phase 4 — Sécurité du pipeline (DevSecOps)

**Commit:** `8a151d3` — feat(phase4): securite pipeline — Trivy, Gitleaks, Vault
**Fichiers:** 30 | **Insertions:** 2 631

### Objectif
Intégrer la sécurité comme étape bloquante du pipeline CI/CD, déployer HashiCorp Vault.

### Ce qui a été fait

#### Pipeline CI/CD (`.github/workflows/ci-cd.yml`)
- 7 jobs: `lint` → `gitleaks` → `test` → `build` → `trivy-scan` → `terraform-validate` → `deploy`
- **Gitleaks:** scan secrets dans git, bloquant en PR
- **Trivy:** scan vulnérabilités CRITICAL/HIGH sur images Docker après build (`--exit-code 1`)
- **pip-audit:** scan dépendances Python (`--strict`)
- **kubeconform + yamllint:** validation manifests K8s + YAML style
- **Concurrency:** `cancel-in-progress: true` (P1)
- **Permissions:** `contents: read` par défaut + per-job escalation (P1)
- **Paths filters:** déclenchement conditionnel par dossier (P1)
- `fail-fast: false` — matrix continue même si un service échoue (P1)

#### HashiCorp Vault
- `k8s/vault/manifests.yaml` — Deployment, Service, ConfigMap script, Job setup, RBAC
- `k8s/vault/values.yaml` — Helm values (dev mode, port 8200)
- `k8s/vault/secret-vault-root.yaml` — placeholder (aucun token commit — P0 #1)
- `scripts/validate-security.sh` — vérifications automatisées
- `scripts/bootstrap-vault-secret.sh` — injecte root token via stdin (P0 #1)

#### Gestion des secrets
- `app/shared/vault_client.py` — client Vault avec `get_secret()`, `SecretUnavailable`
- `app/*/vault_client.py` — copies par service (supprimé Phase 5, unifié shared)
- `.gitleaks.toml` — allowlist durcie: `.*\.md$` retiré, `changeme` retiré (P2)

### Problèmes corrigés (P0/P1/P2)

| ID | Problème | Correction |
|---|---|---|
| **P0 #1** | Root token stocké dans 5 fichiers git | Retiré de tous les fichiers; `bootstrap-vault-secret.sh` injecte via stdin |
| **P0 #2** | DB credentials dans ConfigMap Vault | Déplacés dans Vault KV paths; script lit env (dev) |
| **P0 #3** | `vault_client.py` retournait `{}` silencieux | Maintenant lève `SecretUnavailable`; `_load_secrets()` fail-closed |
| **P0 #5** | CI test job avec `|| true` (masquait échec) | Drops `|| true`, ajoute `pip-audit`, pytest import |
| **P0 #6** | `validate-security.sh` faisait `echo ok` naïf | `set -euo pipefail`, `--ci` gate, `jq` pas python3 |
| **P1** | CI manquait paths/concurrency/perms | `paths:` filters + `concurrency:` + `permissions: contents: read` |
| **P1** | Deploy sans garde-fou | `workflow_dispatch` uniquement + environment `production` |
| **P1** | app/shared pas un vrai package | `pyproject.toml`, `__init__.py`, `pip install -e` |
| **P2** | `.dockerignore` absent | Bloque `.git`/`__pycache__` dans image |
| **P2** | Dependabot absent | Config pip/docker/github-actions |

### Critère de validation
```bash
gitleaks detect           # 0 secret
trivy image <image>       # 0 CRITICAL/HIGH
vault status              # Sealed: false
```

---

## Phase 5 — GitOps ArgoCD

**Commit:** `3914371` — feat(phase5): GitOps ArgoCD + fixes
**Fichiers:** 148 | **Insertions:** 26 783 | **Suppressions:** 469

### Objectif
Faire de Git la seule source de vérité (GitOps).

### Ce qui a été fait

#### ArgoCD (GitOps)
- `k8s/argocd/project.yaml` — AppProject `devops-platform` (destinations: devops-platform)
- `k8s/argocd/app-*.yaml` — 3 Applications (base, users, products, orders)
- SyncPolicy: `prune: true` + `selfHeal: true` + `ServerSideApply: true`
- `CreateNamespace=true` — ns créé automatiquement par ArgoCD

#### K8s manifests réorganisés
- `k8s/apps/base/` — namespace.yaml, hpa.yaml, networkpolicies.yaml, pdbs.yaml, rbac.yaml
- `k8s/apps/{users,products,orders}/` — deployment.yaml par service
- **NetworkPolicies:** `allow-prometheus-scrape` (ingress depuis `monitoring` ns sur port 8000)
- **PDBs:** PodDisruptionBudget 1 minimum disponible
- **topologySpreadConstraints + podAntiAffinity:** dispersion des pods sur nœuds

#### Conteneurs durcis (P2)
Tous les containers (Vault, services, operator):
- `readOnlyRootFilesystem: true`
- `seccompProfile: RuntimeDefault`
- `runAsUser: <non-root>`
- `capabilities.drop: ALL`
- `securityContext.allowPrivilegeEscalation: false`

#### App/shared package fix
- `pyproject.toml` — `packages = ["shared"]` avec `package-dir.shared = "."`
- `setup.py` supprimé (redondant)
- `__init__.py` — corrigé `__all__` typo `"logging"` → `"log_config"`

#### Problèmes corrigés par Phase 5

| Problème | Correction |
|---|---|
| **P1:** CI utilisait images `:latest` | Pinned `ghcr.io/.../users-service:1.0.0` semver |
| **P1:** SA manquant pour services | RBAC `k8s/apps/base/rbac.yaml` avec SA par service |
| **P1:** `vault-sa` non monté sur le Job | SA monté sur Deployment + Job |
| **P1:** `automountServiceAccountToken: false` | → `true` pour auth Kubernetes futur |
| **P1:** vendeur copies `vault_client.py` par service | Supprimées, shared unique |
| **P2:** `.dockerignore` manquant | Bloque `.git`, `__pycache__` |
| **P2:** `gitleaks allowlist` trop large | `.*\.md$` removed, `changeme` removed |
| **P2:** `k8s_reset` non protégé | Gate `when: reset_confirmed|bool` + tag `never` |
| CI: `action SHA` pinned sur commits supprimés | Remplacé par tags `@v5`/`@v3`/`@v2` |
| CI: `pip-audit` vulns fastapi/starlette | Bump fastapi 0.104.1→0.139.0, starlette 0.27.0→1.3.1 |
| CI: Trivy vulns sur packages OS (`jaraco.context`, `wheel`) | Upgrade/uninstall dans Dockerfile |
| CI: LimitRange trop basse | `max.cpu=4`, `max.memory=2Gi` |
| CI: `values.yaml` inclu dans kubeconform | Exclu (`! -name 'values.yaml'`) |
| setuptools: `find_packages()` retournait `[]` | flat-layout: `packages=["shared"]` |
| Trivy OS vulns dans images Docker | `apt upgrade` en builder + uninstall `wheel` + `jaraco.context` |

### Critère de validation
```bash
argocd app get <name>    # Synced + Healthy
```

---

## Phase 6 — Observabilité complète

**Commit:** `a9031be` — feat(phase6): full observability stack — Prometheus, Grafana, Loki, SLOs, dashboards, ArgoCD, CI
**Fichiers:** 46 | **Insertions:** 37 159 | **Suppressions:** 42

### Objectif
Pouvoir répondre à *"combien d'erreurs ?"* (métriques Prometheus) et
*"quelle erreur exactement ?"* (logs Loki).

### Ce qui a été fait

#### Infrastructure observabilité (24 fichiers dans `k8s/observability/`)

| Composant | Type | Détail |
|---|---|---|
| **Namespace** | `monitoring` | PSA restricted, LimitRange 4Gi max/container, ResourceQuota 12Gi limits |
| **Prometheus Operator** | CRDs + Deployment | v0.74.0, 8 CRDs (ServiceMonitor, PrometheusRule, Alertmanager...), 35 417 lignes |
| **Prometheus** | StatefulSet | v2.51.0, 15d retention, 10Gi PVC, 15s scrape interval |
| **Alertmanager** | StatefulSet | v0.27.0, 2Gi PVC, webhook routing |
| **Grafana** | Deployment | 11.1.0, datasources provisionnés, dashboard sidecar k8s-sidecar |
| **Loki** | StatefulSet | 3.1.1, single-node, 10Gi PVC, 7d retention, tsdb schema v13 |
| **Promtail** | DaemonSet | 3.1.1, 1 pod/nœud, parse logs JSON, push Loki |
| **SLO Rules** | PrometheusRule | 3 alertes SLO |

#### ServiceMonitors (scrape déclaratif)
- `k8s/apps/*/servicemonitor.yaml` — 3 ServiceMonitors (users, products, orders)
- Namespace `monitoring` (découvert par Prometheus Operator)
- NamespaceSelector → `devops-platform` (où les services vivent)
- Interval 15s, scrape sur port `http` (80), path `/metrics`

#### Instrumentation applicative
- **prometheus_client.Counter** retiré de chaque `main.py` (3 services × 4 inc() = 12 calls supprimés)
- **prometheus-fastapi-instrumentator** installé (dans `app/*/requirements.txt` + `app/shared/requirements.txt`)
- Nouveaux metrics exposés:
  - `http_requests_total{handler, status_code, method, http_version}` — par endpoint + status
  - `http_request_duration_seconds_bucket{le, handler}` — histogram pour calcul P95/P99
  - `http_request_duration_seconds_count/_sum` — latence totale
- Endpoints exclus de l'instrumentation: `/metrics`, `/healthz`, `/readyz`

#### Dashboards Grafana (3 ConfigMaps + sidecar)
- **01-infra-overview:** nœuds Ready, pods totaux, CPU/memory cluster + par namespace
- **02-app-performance:** RPS par endpoint, P95/P99 latence, bucket heatmap
- **03-error-rate:** taux 5xx avec seuil SLO 1%, compteur 5xx, top endpoints erreur

#### Logs (remplacement ELK par Loki)
- ELK trop lourd pour cluster 3 nœuds (`LimitRange 2Gi` bloque Elasticsearch)
- **Loki** single-node avec stockage filesystem, rétention 7 jours
- **Promtail** parse `level`, `message`, `timestamp` du JSON stdout (Phase 5 `python-json-logger`)
- Labels: `namespace`, `service`, `container`, `level`
- Requête Grafana: `{service="users-service"} |= "ERROR"`

#### ArgoCD GitOps
- 8 nouvelles Applications ArgoCD (observability-base, prometheus, alertmanager, grafana, grafana-dashboards, loki, promtail, slo-rules)
- Destination: `monitoring` ns (whitelist ajoutée au AppProject)

#### CI
- kubeconform exclut `crds.yaml` (35k lignes CRDs = pas un manifest K8s standard)
- Déploiement: smoke-test `/metrics` vérifie `http_request_duration_seconds_bucket` présent

#### Résolution blockers Phase 5
- _canary/Flagger/Istio retirés volontairement — section conservée pour traçabilité historique_

#### Décisions vs spec originale

| Spec (`DevOps_Central_Platform_Etapes_Implementation.md`) | Réalisé | Justification |
|---|---|---|
| Elasticsearch + Logstash + Kibana + Filebeat | **Loki + Promtail** | ELK > 2Gi RAM, cluster local 3 nœuds pas suffisant. JSON logging → stdout déjà prêt pour Promtail |
| Dashboards construits manuellement UI | **ConfigMaps label `grafana_dashboard=1` + k8s-sidecar** | Versionné git, auto-importé, reproductible |
| Règles AlertManager en config statique | **PrometheusRule CRD** | Versionné git, sync ArgoCD |

### SLO Alert règles
| Nom | Seuil | Query |
|---|---|---|
| `SLOAvailabilityBreach` | Disponibilité < 99.9% (30 jours) | `avg_over_time(up[30d])` |
| `SLOLatencyP95Breach` | P95 > 200ms (5 min) | `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))` |
| `SLO5xxErrorRateBreach` | 5xx > 1% (5 min) | `rate 5xx / rate total * 100` |

### Critère de validation
```bash
kubectl port-forward svc/prometheus -n monitoring 9090:9090
# http://localhost:9090/-/healthy → OK, Targets all UP

kubectl port-forward svc/grafana -n monitoring 3000:3000
# http://localhost:3000 → 3 dashboards avec données live

kubectl port-forward svc/loki -n monitoring 3100:3100
# Grafana Explore → {service="users-service"} → logs JSON

kubectl logs -n istio-system deploy/flagger | grep prometheus  # historique, Flagger retiré
# Plus d'erreur "connection refused prometheus:9090"
```

---

## Synthèse de tous les problèmes corrigés

### P0 — Bloquants (sécurité critique)

| ID | Problème | Phase | Correction |
|---|---|---|---|
| P0 #1 | Root token Vault dans 5 fichiers git | 4 | Retiré; injecté via stdin → bootstrap-vault-secret.sh |
| P0 #2 | DB credentials dans ConfigMap | 4 | Déplacés dans Vault KV paths |
| P0 #3 | `vault_client.py` retournait `{}` silencieux | 4 | Lève `SecretUnavailable`, `_load_secrets()` fail-closed |
| P0 #4 | tfstate + inventory dans git | 1&2 | `.gitignore` bloque, backend S3 documenté |
| P0 #5 | CI `|| true` masquait échec tests | 4 | Drop `|| true`, ajout pip-audit + pytest |
| P0 #6 | `validate-security.sh` no-op | 4 | `set -euo pipefail`, --ci gate, jq |

### P1 — Élevés

| Problème | Phase | Correction |
|---|---|---|
| CI sans paths/concurrency/perms | 4 | `paths:` filters, `concurrency:`, `permissions: contents: read` |
| CI images `:latest` non reproductibles | 5 | Pinned semver `:1.0.0` |
| app/shared pas un package | 5 | `pyproject.toml`, `__init__.py` |
| Deploy sans garde-fou | 4 | `workflow_dispatch` + environment `production` |
| Per-service SA manquants | 5 | RBAC par service dans `rbac.yaml` |
| `automountServiceAccountToken: false` | 5 | → `true` (futur auth k8s) |
| NetworkPolicies absentes | 5 | Créées (`allow-prometheus-scrape`, `deny-all-ingress`) |
| PDBs absents | 5 | Créés (1 min disponible) |

### P2 — Moyens

| Problème | Phase | Correction |
|---|---|---|
| `.dockerignore` absent | 4 | Bloque `.git`/`__pycache__` |
| Dependabot absent | 4 | Config pip/docker/github-actions |
| Gitleaks allowlist trop large | 4 | `.*\.md$` retiré, `changeme` retiré |
| TF pas de validation blocks | 1&2 | `validation` sur network_cidr, worker_count... |
| TF `gateway_ip` codé dur | 1&2 | `cidrhost(var.network_cidr, 1)` |
| TF lock constraint `~> 0.7` (inexistante) | 1&2 | `~> 0.9` |
| UID/GID libvirt codés dur | 1&2 | `var.libvirt_volume_owner_uid/gid` |
| Conteneurs non durcis | 5 | `readOnlyRootFilesystem`, `seccomp RuntimeDefault`, `drop ALL` |
| `k8s_reset` sans gate | 1&2 | `when: reset_confirmed|bool` + tag `never` |
| kubeadm join 0644 | 1&2 | → 0600 |
| Rôles Ansible pas Galaxy-compliant | 1&2 | `defaults/main.yml` + `meta/main.yml` |
| `values.yaml` scanné par kubeconform | 5 | Exclu |
| `setup.py` redondant | 5 | Supprimé |
| ELK lourd pour cluster local | 6 | Remplacé par Loki + Promtail |
| LimitRange max memory 2Gi bloque ELK | 6 | Nouveau ns `monitoring` avec 4Gi max |
| Starlette vulns (PYSEC-2026-*) | 5 | Bump 0.27.0→1.3.1 |
| Instrumentator 7.0.0 cassait starlette 1.3.1 | 6 | Pinné 6.1.0 (compatible starlette>=1.0) |

---

## Arborescence finale (`clean-main`)

```
app/
├── orders-service/        Dockerfile, main.py, requirements.txt
├── products-service/      Dockerfile, main.py, requirements.txt
├── shared/                log_config.py, vault_client.py, pyproject.toml
└── users-service/         Dockerfile, main.py, requirements.txt
ansible/
├── playbook.yml, requirements.yml, inventory.ini
└── roles/                 docker, k8s_common, k8s_master, k8s_reset, k8s_worker
k8s/
├── apps/                  base/, users/, products/, orders/ (deployments + canaries)
├── argocd/                project.yaml + app-*.yaml (14 Applications)
├── (istio-flagger/ supprimé)
├── observability/         base/, prometheus/, alertmanager/, grafana/,
│                          grafana-dashboards/, loki/, promtail/, rules/
└── vault/                 manifests.yaml, values.yaml, scripts
terraform/                 main.tf, variables.tf, outputs.tf, backend.tf
.github/workflows/         ci-cd.yml (8 jobs)
scripts/                   bootstrap-vault-secret.sh, validate-security.sh
```

---

## Commandes de déploiement

```bash
# 1. CRDs Prometheus Operator (obligatoire en premier)
kubectl apply -f k8s/observability/prometheus/crds.yaml

# 2. Namespace + quotas
kubectl apply -f k8s/observability/base/namespace.yaml

# 3. Stack Monitoring
kubectl apply -f k8s/observability/prometheus/  # operator + Prometheus + RBAC + Service
kubectl apply -f k8s/observability/alertmanager/  # Alertmanager + Service + config
kubectl apply -f k8s/observability/grafana/  # Grafana + datasources + RBAC
kubectl apply -f k8s/observability/grafana-dashboards/ # 3 dashboards

# 4. Stack Logs
kubectl apply -f k8s/observability/loki/   # Loki StatefulSet + Service
kubectl apply -f k8s/observability/promtail/  # Promtail DaemonSet

# 5. Règles SLO
kubectl apply -f k8s/observability/rules/

# 6. ArgoCD sync (si GitOps)
kubectl apply -f k8s/argocd/app-observability-base.yaml
kubectl apply -f k8s/argocd/app-prometheus.yaml
kubectl apply -f k8s/argocd/app-alertmanager.yaml
kubectl apply -f k8s/argocd/app-grafana.yaml
kubectl apply -f k8s/argocd/app-grafana-dashboards.yaml
kubectl apply -f k8s/argocd/app-loki.yaml
kubectl apply -f k8s/argocd/app-promtail.yaml
kubectl apply -f k8s/argocd/app-slo-rules.yaml
```

---

## A faire (follow-up production)

- Vault Agent Injector annotations (au lieu de `VAULT_TOKEN` env)
- Remplacer images semver par `@sha256:<digest>` (P1 build workflow)
- `terraform.tfvars.example` + variables override documenté
- Tests unitaires pytest + TestClient pour chaque service (dossier `tests/`)
- Vault: raft storage + auto-unseal (production)
