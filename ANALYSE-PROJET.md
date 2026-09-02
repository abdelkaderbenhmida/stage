# Projet : Plateforme DevOps centralisée (homelab KVM)

Analyse basée uniquement sur le code source (pas de .md/.pdf).

C'est un projet "tout-en-un" de **plateforme DevOps** déployée sur un homelab virtuel. L'objectif est de reproduire une stack d'entreprise complète : du provisionnement de machines virtuelles jusqu'au déploiement GitOps de microservices, avec observabilité et sécurité.

## Architecture globale

```
[App: 3 microservices FastAPI]  →  containerisés (multi-stage, non-root)
        │
[Kubernetes]  ← provisionné par Terraform (libvirt/KVM) + Ansible
        │
[ArgoCD]  → GitOps (déploiement auto à partir du repo)
        │
[Observabilité]  → Prometheus + Grafana + Alertmanager + ELK
        │
[Vault]  → gestion centralisée des secrets
        │
[CI/CD GitHub Actions]  → lint → secrets → tests → build → scan → deploy
```

## 1. Les 3 microservices (`app/`)

Trois services FastAPI **quasi identiques**, chacun avec des endpoints REST mock (données en dur, pas de vraie BDD).

| Service | Secret propre | Endpoint data |
|---------|--------------|---------------|
| `users-service` | `JWT_SECRET_KEY` | `/users` (Alice, Bob) |
| `products-service` | `API_KEY` | `/products` (Laptop, Mouse) |
| `orders-service` | `PAYMENT_GATEWAY_KEY` | `/orders` |

**Endpoints communs à chacun :**
- `GET /` → métadonnées (service, version, `vault_configured`)
- `GET /livez` → liveness (process vivant)
- `GET /readyz` → readiness : **renvoie 503 si Vault est injoignable** (`main.py:88`)
- `GET /metrics` → métriques Prometheus (via `prometheus_fastapi_instrumentator`), avec Historique `http_request_duration_seconds`

### Gestion des secrets — **fail-closed** (`main.py:26-59`)
C'est le cœur de la conception :
- Au démarrage, chaque service va chercher `DATABASE_URL` + sa clé dans Vault (`get_secret()`).
- **En production** : si le secret manque → `SystemExit`, le conteneur refuse de démarrer plutôt qu'utiliser une valeur de secours (anti-pattern P0 corrigé).
- **En dev** (`ENVIRONMENT=dev`) : repli sur sqlite-mémoire + token éphémère, avec logs d'avertissement.

## 2. Bibliothèque partagée (`app/shared/`)

**`vault_client.py`** (239 lignes) — client Vault via `hvac` :
- Résolution de token : injecteur Vault Agent (`/vault/secrets/token`) → `VAULT_TOKEN` → env dev
- Chemin de secret : `secret/data/devops-platform/<service>`
- Cache mémoire (`@lru_cache`) + `reload_secrets()` pour rotation
- `get_secret()` : ordre Vault → env → default (default **uniquement** valeurs non sensibles dev)
- `vault_health()` : pour les probes readiness

**`log_config.py`** — logging structuré **JSON** vers stdout (`LOG_FORMAT=json`/`plain`), centralisé pour que tous les services émettent le même format pour les agrégateurs (ELK/Loki).

**`config.py`** — AppConfig (charge `environment`, etc.)

## 3. Docker (durcissement)

`app/users-service/Dockerfile` (repris pour les 3) :
- **Multi-stage** : builder isole pip caches, image finale légère
- **Utilisateur non-root** (`appuser`) + `COPY --chown`
- Python 3.11-slim, digest pin possible via `--build-arg BASE_IMAGE`
- **HEALTHCHECK** en défense-in-depth (en plus des probes k8s)
- Suppression des wheels/jaraco pour éviter les fichiers superflus
- **Build context = `app/`**, pas le répertoire du service (pour voir `shared/`)

## 4. Provisionnement Infra

**Terraform (`terraform/main.tf`, `~> 1.5`)** — provider `dmacvicar/libvirt` :
- `libvirt_network` : réseau NAT avec DHCP + DNS
- `libvirt_domain` : VMs KVM (1 master + N workers) avec cloud-init
- Génère l'inventaire Ansible (`inventory.generated.ini`) à partir des IP calculées (master=`.10`, workers=`.11+`)
- Precondition guarde l'existence d'une clé SSH

**Ansible (`ansible/playbook.yml`)** — rôles :
- `docker` : installation runtime
- `k8s_common` / `k8s_master` / `k8s_worker` : bootstrap du cluster Kubernetes
- `k8s_reset` : **opt-in strict** — nécessite `--tags reset` **ET** `-e reset_confirmed=true` (anti-pattern P2 corrigé : un `ansible-playbook` par erreur ne détruit plus le cluster)

## 5. Kubernetes + GitOps

**kustomize** (`k8s/apps/`) :
- Déploiements/Services par service (`users/`, `products/`, `orders/`)
- `base/` : namespace, **networkpolicies** (micro-segmentation réseau), **PDB**, **RBAC**, **HPA** (auto-scaling)
- Overlays : `dev/`, `staging/`, `prod/`

**ArgoCD (`k8s/argocd/`)** : Applications GitOps pour chaque service + observabilité + install. Les manifestes vivent dans le repo, ArgoCD les sync automatiquement.

**Vault (`k8s/vault/`)** : manifestes d'installation, policy `.hcl`, secret root temporaire.

**Policies (`k8s/policies/`)** : conftest `.rego` (policy-as-code, ex. `disallow-latest-images`, readOnlyRootFilesystem, drop ALL caps).

## 6. Observabilité (`k8s/monitoring/`)

- **Prometheus** + operator + CRDs + kube-state-metrics + kubelet scrape
- **Grafana** + dashboards : infra-overview, app-performance, error-rate, infra-detail
- **Alertmanager** : config + règles d'alerte
- **ELK stack** : Elasticsearch, Kibana, Logstash (values Helm) + **Filebeat** daemonset pour collecter les logs des pods → ELK

## 7. CI/CD (`ci-cd.yml`) — GitOps

Pipeline en cascade (dépendances explicites, `fail-fast: false`) :

```
lint → gitleaks → test → build → trivy-scan → [workflow_dispatch] deploy
                              ↘ terrainform-validate
```

1. **lint** : ruff (services), terraform fmt, yamllint + kubeconform (validation schéma k8s) + conftest (policy)
2. **gitleaks** : scan des secrets (bloquant, commente les PRs)
3. **test** : pytest (TestClient FastAPI, env dev), **pip-audit --strict** (défaut si dépendance vulnérable), validation des imports
4. **build** : matrix 3 services → buildx, push vers GHCR, **tags digest/SHA immuables** (pas de `:latest` hors branche par défaut), SBOM + provenance
5. **trivy-scan** : image par digest (évite la course au tag), scan **bloquant sur CRITICAL/HIGH** (`--exit-code 1`), SARIF upload, génération SBOM SPDX
6. **deploy** : uniquement `workflow_dispatch` (manuel) + env protégée `production`. Server-side apply via kustomize, digests épinglés, rollout status, smoke-test `/metrics`
7. **load-test** (schedule) : k6

Durcissements notables : `concurrency: cancel-in-progress` (évite les courses de tag), permissions least-privilege, nightly drift scan.

## 8. Scripts de validation (`scripts/`)

- `validate-platform.sh` — 7 vérifications (dont RHEL self-heal + rollback, destructif)
- `validate-security.sh` — 4 vérifications (gitleaks/trivy/vault/token)
- `generate-inventory.sh` — régénère `inventory.ini` depuis terraform (jamais à la main)
- `smoke-test.sh`, `bootstrap-vault-secret.sh`, `stress-hpa.sh`/`stress-panel.py` (charge HPA)

## Points d'attention (d'après le code)

- **Données factices** : les microservices sont des stubs (pas de vraie BDD, pas de logique métier). Le livrable réel est l'**infrastructure et le pipeline**, pas l'app elle-même.
- `tests/k6/load-test.js` est référencé dans CI mais n'existe pas (le job schedule échoue).
- `terraform.tfstate` et `*.tfvars` jamais commités (whitelistés `.gitleaks.toml`).

## Résumé

C'est un projet d'apprentissage/démonstration de bout-en-bout d'une **plateforme DevOps sécurisée** — provisionnement KVM, cluster k8s, microservices à secrets dynamiques, observabilité complète (Prometheus/Grafana/ELK), et pipeline GitOps durci — servant probablement de support pour un rapport universitaire (les fichiers `rapport.tex` sont le rapport LaTeX associé).
