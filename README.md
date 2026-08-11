# DevOps Central Platform

[![CI/CD](https://github.com/<owner>/<repo>/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/<owner>/<repo>/actions/workflows/ci-cd.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

End-to-end DevOps platform built around **3 FastAPI microservices** (Users, Products, Orders). The project demonstrates the complete modern DevOps chain — Infrastructure as Code, automated configuration, containerization, Kubernetes orchestration, DevSecOps hardening, GitOps, and full observability (metrics + centralized logs) — deployed on a realistic libvirt/KVM homelab.

Built in **7 phases**, each a self-contained deliverable:

| Phase | What it delivers | Key tools |
|---|---|---|
| 1 — Infrastructure as Code | 3 VMs (1 master + 2 workers) on libvirt/KVM, NAT network, cloud-init | Terraform |
| 2 — Configuration | Docker, containerd, kubeadm bootstrap, Calico CNI | Ansible |
| 3 — Microservices | 3 FastAPI services with health probes + Prometheus `/metrics` | Python, Docker |
| 4 — Secret management | HashiCorp Vault dev mode, fail-closed client, KV v2 policies | Vault |
| 5 — GitOps | ArgoCD Application CRDs auto-sync all manifests from git | ArgoCD |
| 6 — Observability | Prometheus, Grafana, ELK, AlertManager SLO rules | monitoring stack |
| 7 — Validation | Automated 7-check platform (+ self-heal/rollback) + 4-check security | bash scripts |

> Full specification (French): [`docs/DevOps_Central_Platform_Description.md`](docs/DevOps_Central_Platform_Description.md) and [`docs/DevOps_Central_Platform_Etapes_Implementation.md`](docs/DevOps_Central_Platform_Etapes_Implementation.md)

---

## Features

- **3 FastAPI microservices** — Users, Products, Orders; each exposes `GET /<resource>`, `/livez` (liveness), `/readyz` (readiness) and Prometheus `/metrics`
- **Shared library** `app/shared/` — Vault client (`get_secret()` fail-closed), structured logging, environment-driven `AppConfig`
- **K8s hardening** — NetworkPolicies, RBAC, PodDisruptionBudgets (`maxUnavailable: 1`), HPAs (2–5 replicas, CPU 70% / custom 80%)
- **GitOps via ArgoCD** — `prune: true`, `selfHeal: true`, every workload reconciles from this repo
- **Observability** — Prometheus 15s scrape, 4 Grafana dashboards, ELK pipeline (Filebeat → Logstash → Elasticsearch → Kibana), AlertManager recording + SLO rules
- **DevSecOps CI** — Gitleaks, Trivy (blocking on CRITICAL/HIGH), `pip-audit --strict`, digest-pinned images, least-privilege workflow
- **Validation** — 7-check platform validator with self-heal + rollback; 4-check security validator
- **Opt-in destructive ops** — the cluster reset Ansible play requires both `--tags reset` **and** `-e reset_confirmed=true`

## Tools and their role in this project

### Terraform — Phase 1, Infrastructure as Code

Provisions the homelab on libvirt/KVM using the [`dmacvicar/libvirt`](https://registry.terraform.io/providers/dmacvicar/libvirt) provider. Pinned to `~> 1.5` (upper bound `required_version` in `terraform/main.tf`; 1.6+ is not tested).

What it creates:
- `libvirt_network.platform` — NAT network on an RFC1918 subnet
- `libvirt_volume.base` — base qcow2 image, then `libvirt_volume.node` per VM (one per master/worker)
- `libvirt_cloudinit_disk.init` + `network-config.tpl` — injected per-node for SSH keys, hostname, network
- `libvirt_domain.node` — 3 VMs (master-01, worker-01, worker-02)
- `local_file.ansible_inventory` — renders `terraform/inventory.generated.ini` from `inventory.tpl` (the only source `scripts/generate-inventory.sh` consumes; `ansible/inventory.ini` is generated, never hand-edited)

State is stored **locally** (no remote backend in `terraform/backend.tf`) — suitable for homelab; switch to S3 + DynamoDB for cloud/CI use. `terraform.tfstate` and `*.tfvars` are gitignored and whitelisted in `.gitleaks.toml` only as defense-in-depth.

### Ansible — Phase 2, Configuration

`ansible/playbook.yml` has 5 plays — applied in order:

| Play | Hosts | Role | Purpose |
|---|---|---|---|
| 1 | all | `docker` | installs Docker + containerd (the container runtime Kubernetes uses) |
| 2 | all | `k8s_common` | kernel modules, sysctl, kubelet prerequisites, kubeadm |
| 3 | `masters` | `k8s_master` | `kubeadm init`, CNI install (Calico v3.26.1), admin kubeconfig |
| 4 | `workers` | `k8s_worker` | `kubeadm join` — `serial: 1` so workers join one-by-one (stable bootstrap) |
| 5 | all | `k8s_reset` (opt-in) | wipes the cluster. Requires `--tags reset` **and** `-e reset_confirmed=true` |

Pinned versions (`ansible/group_vars/all.yml`): k8s `1.28`, Calico `v3.26.1`, pod CIDR `192.168.0.0/16`, service CIDR `10.96.0.0/12`, CRI `containerd`. Tested with `molecule` (`ansible/molecule.yml`).

### Docker — Phase 3, Containerization

Multi-stage `Dockerfile` per service. Two non-obvious rules that agents/CI enforce:
1. **Build context is `app/`, not per-service** — required so services can `import shared.`. Always build with `-f app/<svc>/Dockerfile app/`.
2. **Non-root user + HEALTHCHECK** — required by the conftest policies and the pod `liveness`/`readiness` probes.

Images are pushed to GitHub Container Registry (GHCR), tagged `branch-name` + `commit-<sha>`. `:latest` is only emitted on the default branch (no mutable tags on feature branches → no tag races in the Trivy matrix).

### Kubernetes — orchestration

Kubernetes 1.28 with containerd + Calico CNI. `k8s/apps/` is organized with kustomize:
- `base/` — shared concerns: `namespace.yaml`, `hpa.yaml`, `pdbs.yaml` (maxUnavailable 1), `rbac.yaml`, `networkpolicies.yaml`, `kustomization.yaml`
- `users/` `products/` `orders/` — per-service Deployment + Service + image override
- `overlays/{dev,staging,prod}` — environment-specific patches (namespaces, replica counts)
- `k8s/apps/kustomization.yaml` aggregates `base/` + the 3 service kustomizations and pins images to `ghcr.io/<owner>/<svc>`

### HashiCorp Vault — Phase 4, Secrets

Runs in **dev mode** (root token injected out-of-band via `scripts/bootstrap-vault-secret.sh`, never committed). The client pattern is the important part:

- `app/shared/vault_client.py` exposes `get_secret(name, default=None)`
- Resolution order: **Vault secret → env var → default → raise `SecretUnavailable`**
- On a Vault error it **fails closed**: services exit rather than start without resolvable secrets. The only dev override is `ENVIRONMENT=dev` (sqlite memory, ephemeral token, env-var fallback allowed)
- KV v2 policy in `k8s/vault/vault-policy.hcl`: each service gets `read` on `secret/data/devops-platform/${svc}` + `read,list` on the metadata path — least privilege per service

### ArgoCD — Phase 5, GitOps

After a one-time `kubectl apply -k k8s/argocd/install/`, ArgoCD reconciles the rest. Every workload has an `Application` CRD in `k8s/argocd/applications/` pointing at `https://github.com/<owner>/<repo>.git`, with:

```yaml
syncPolicy:
  automated:
    prune: true     # deletes resources removed from git
    selfHeal: true  # reverts manual kubectl edits
```

Apps synced: 3 servicios (users/products/orders), base apps, Prometheus, Grafana, Grafana dashboards, AlertManager, ELK, SLO rules, plus ArgoCD self-management.

### Prometheus + Grafana — Phase 6, Metrics

- **Prometheus** with `scrapeInterval: 15s`, `serviceMonitorSelector: {}` picks up **all** ServiceMonitors in the cluster automatically.
- **Grafana** ships pre-configured datasources (Prometheus at `prometheus.monitoring.svc:9090`, Elasticsearch at `elasticsearch.monitoring.svc:9200` — both `access: proxy`) and 4 dashboards: `01-infra-overview`, `02-app-performance`, `03-error-rate`, `04-infra-detail`.
- `kube-state-metrics` + kubelet metrics enrich the dashboards.
- Each FastAPI service exposes `http_request_duration_seconds` histogram via `prometheus-fastapi-instrumentator` (excludes `/livez`, `/readyz`, `/metrics`).

### ELK — Phase 6, Logs

Filebeat (DaemonSet on every node) → Logstash → Elasticsearch → Kibana.
Service logs are structured JSON (`shared/log_config.py`) so they are searchable and filterable in Kibana.

### AlertManager — Phase 6, Alerting

`k8s/monitoring/alertmanager/rules.yaml` defines the `slo-rules` PrometheusRule with recording rules (`slo-availability-recording` via `avg_over_time(up{job=~"users-service|products-service|orders-service"}[30d])`) and SLO alert groups wired to AlertManager.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ GitHub Actions CI  (lint → gitleaks → test → build → trivy)      │
│   builds 3 images on GHCR (digest-pinned, no :latest on branches) │
└──────────────────────────────┬───────────────────────────────────┘
                               │ pull
┌──────────────────────────────▼───────────────────────────────────┐
│ K8s cluster — homelab libvirt/KVM, 1 master + 2 workers          │
│                                                                  │
│  ┌────────────┐  ┌─────────────┐  ┌────────────┐                 │
│  │ users-svc  │  │products-svc │  │ orders-svc │   FastAPI,     │
│  │   :8000    │  │    :8000     │  │   :8000    │   HPA, PDB,    │
│  └─────┬──────┘  └─────┬───────┘  └─────┬──────┘   NetworkPolicies│
│        └───────┬───────┘                 │                         │
│                ▼                         ▼                         │
│  ┌─────────────────────┐  ┌─────────────────────┐                 │
│  │ ArgoCD (GitOps)     │  │ Vault (fail-closed) │                 │
│  │ prune + selfHeal    │  │ KV v2 per-service   │                 │
│  └─────────────────────┘  └─────────────────────┘                 │
│                                                                  │
│  Prometheus 15s ─► Grafana (4 dashboards) ─► AlertManager (SLO)   │
│  Filebeat ──► Logstash ──► Elasticsearch ──► Kibana              │
└──────────────────────────────────────────────────────────────────┘
```

## Repository layout

```
├── terraform/                # main.tf, backend.tf, variables.tf, outputs.tf, inventory.tpl,
│                             # cloud-init.tpl, network-config.tpl, terraform.tfvars.example
├── ansible/                  # playbook.yml, roles/, group_vars/, inventory.ini (generated), molecule.yml
├── app/
│   ├── users-service/        # FastAPI CRUD (users) + Dockerfile + main.py
│   ├── products-service/     # FastAPI CRUD (products)
│   ├── orders-service/       # FastAPI CRUD (orders)
│   └── shared/               # vault_client, log_config, config — installed via `pip install -e app/shared/`
├── k8s/
│   ├── apps/                 # base/ + per-service kustomizations + overlays (dev/staging/prod)
│   ├── monitoring/           # Prometheus, Grafana (4 dashboards), ELK, AlertManager, kube-state-metrics, kubelet
│   ├── argocd/               # Application CRDs + install kustomization + project.yaml
│   ├── vault/                # manifests, vault-policy.hcl, secret-vault-root (template), values
│   └── policies/             # Conftest policy-as-code (run in audit mode)
├── scripts/
│   ├── validate-platform.sh        # 7 checks + self-heal + rollback
│   ├── validate-security.sh        # gitleaks / trivy / vault / token
│   ├── generate-inventory.sh       # TF → ansible/inventory.ini (`--no-refresh` skips TF refresh)
│   ├── bootstrap-vault-secret.sh    # injects dev Vault root token out-of-band
│   ├── bootstrap-elasticsearch-secret.sh
│   ├── smoke-test.sh
│   └── stress-hpa.sh               # HPA scaling test
├── .github/workflows/ci-cd.yml    # 8 hardened jobs
├── tests/                          # pytest (FastAPI TestClient under ENVIRONMENT=dev)
└── docs/                           # Specs (FR) + runbooks
```

## Requirements

| Tool | Version |
|---|---|
| Terraform | `~> 1.5` (pinned; do not use 1.6+) |
| Kubernetes | 1.28 |
| Python | 3.11 |
| Docker / kubectl / helm / jq | latest stable |

**Homelab footprint:** 3 VMs on libvirt/KVM — ~8 GB RAM + 4 vCPU per node, or an equivalent cloud setup.

## Quick start

### 1. Build images

> The Docker build context is `app/`, not per-service — required so the services see `shared/`.

```bash
for svc in users-service products-service orders-service; do
  docker build -t "${svc}:1.0.0" -f "app/${svc}/Dockerfile" app/
done
```

### 2. Provision infrastructure and configure the cluster

```bash
cd terraform && terraform init && terraform apply
scripts/generate-inventory.sh          # renders ansible/inventory.ini from TF state
cd ../ansible && ansible-playbook playbook.yml
# To wipe the cluster (opt-in): ansible-playbook playbook.yml --tags reset -e reset_confirmed=true
```

### 3. Bootstrap secrets and deploy

```bash
kubectl apply -f k8s/apps/base/namespace.yaml
kubectl apply -f k8s/vault/manifests.yaml
scripts/bootstrap-vault-secret.sh      # injects a dev token out-of-band (never in git)
kubectl apply -f k8s/apps/
kubectl apply -k k8s/argocd/install/   # ArgoCD bootstrap (once) — then auto-syncs the rest
# ArgoCD then auto-syncs all application manifests
```

### 4. Validate

```bash
scripts/validate-platform.sh                 # summary output
scripts/validate-platform.sh --ci            # gating — exit 1 on any failure
scripts/validate-platform.sh --skip-incident # skip destructive self-heal/rollback
scripts/validate-security.sh                 # gitleaks / trivy / vault / token
```

## CI/CD pipeline

`.github/workflows/ci-cd.yml` — 8 jobs, `fail-fast: false`, `concurrency: cancel-in-progress` (new runs on the same ref cancel old ones, preventing tag races in the matrix jobs).

```
         lint ──┐                                ┌──► deploy  (workflow_dispatch only)
                ├──► gitleaks (blocking) ─┐      │
                │                         ├──► test ─► build ─► trivy-scan ─┤
         terraform-validate ──────────────┘                                │
                                                                          └──► load-test (schedule only)
```

**Triggers:** pushes/PRs on `main`, `develop`, `clean-main`, `secondary`, **only** when paths in the filter change (`app/`, `k8s/`, `terraform/`, `ansible/`, `scripts/`, `.gitleaks.toml`, the workflow itself). Docs- and tests-only changes skip CI. Nightly `schedule:` runs a dependency-drift re-check and a k6 load test.

**Permissions:** least-privilege — `contents: read` by default; jobs that need more (`packages: write`, `security-events: write`, `pull-requests: write`) declare it explicitly.

### Job-by-job

**lint** (parallel with gitleaks)
- `ruff check` over `app/shared/` and each service directory
- `terraform fmt -check -recursive terraform/` (TF 1.5.7)
- `yamllint` over `k8s/`
- `kubeconform -strict` against `find k8s/` excluding `*values*.yaml`, `Chart.yaml`, `crds.yaml`
- `conftest` against `k8s/policies/` — run in **audit mode** (non-blocking)

**gitleaks** (blocking, in parallel with lint)
- full-history scan (`fetch-depth: 0`), config `.gitleaks.toml`
- comments on PRs, uploads a report artifact, fails the pipeline on findings

**terraform-validate** (parallel)
- matrix `[ "1.5.7" ]` (pinned; `required_version ~> 1.5` in `backend.tf` forbids 1.6.x)
- `terraform init -backend=false` then `terraform validate`

**test** (`needs: [lint, gitleaks]`)
- `pip install -e app/shared/` + each service's `requirements.txt`
- `pip-audit --strict` over all `requirements.txt` — a pinned dependency with a known CVE fails the build
- `python -c "from shared.vault_client import get_secret; from shared.log_config import setup_logging; from shared.config import AppConfig"` verifies the shared package
- `ENVIRONMENT=dev LOG_FORMAT=plain VAULT_ADDR="" pytest -q tests/ -v`
- real import check per service: `cd app/$svc && PYTHONPATH=../shared:. ENVIRONMENT=dev python -c "import main"`
- Dockerfile build validation for each service (context `app/`)
- uploads build logs as artifacts on failure

**build** (`needs: test`, matrix of 3 services)
- GHA build-cache (`cache-from: type=gha`, `cache-to: type=gha,mode=max`)
- tags: `branch-name`, `commit-<sha>`, `:latest` only on the default branch, plus semver
- output digest is captured — Trivy scans the **digest**, not `:latest`, avoiding a tag race between matrix build rows
- provenance + SBOM enabled on each image

**trivy-scan** (`needs: build`, parallel matrix)
- pulls the tag (resolved from `github.ref_name`)
- **non-blocking** SARIF scan first (so the file always exists) → uploads to GitHub Security tab (`continue-on-error`)
- **blocking** scan: `--severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed --vuln-type os,library`
- SPDX SBOM uploaded as a 30-day artifact

**deploy** (`needs: [trivy-scan, terraform-validate]`, `if: github.event_name == 'workflow_dispatch'`, protected `production` environment)
- `kustomize edit set image` to pin each service to its digest
- pre-deploy validation: `kustomize build k8s/apps/ | kubeconform -strict`
- `kubectl apply --server-side` for Vault + apps
- `kubectl rollout status` for vault + the 3 services (120s timeout each)
- `/metrics` smoke test from a `curlimages/curl` pod — fails if the histogram metric is missing

**load-test** (`if: github.event_name == 'schedule'`)
- k6 against `tests/k6/load-test.js` (⚠ not yet in the repo — the nightly job will fail until it's added)

## Observability & SLOs

| SLI | SLO | Implementation |
|---|---|---|
| Availability | ≥ 99.9% over 30 days | `avg_over_time(up{job=~"users-service\|products-service\|orders-service"}[30d])` recording rule |
| Latency P95 | < 200 ms | `http_request_duration_seconds` histogram |
| 5xx error rate | < 1% | AlertManager SLO group |
| MTTD | < 2 min | Prometheus 15s scrape + AlertManager |
| MTTR | < 30 min | runbooks + HPA/PDB keep serving during incidents |

Rules in [`k8s/monitoring/alertmanager/rules.yaml`](k8s/monitoring/alertmanager/rules.yaml).

## DevSecOps

- Gitleaks blocking in CI **and** as a pre-commit hook (`ruff`, `yamllint`, `gitleaks`, `terraform fmt`/`validate`)
- Trivy blocks the pipeline on CRITICAL/HIGH (SARIF + SPDX SBOM produced regardless)
- `pip-audit --strict` — pin a vulnerable dep, the pipeline fails
- No mutable `:latest` on non-default branches; deploy pins digests via kustomize
- Vault fail-closed — services exit rather than start without resolvable secrets
- `terraform.tfstate` / `*.tfvars` / `ansible/inventory.ini` gitignored and generated
- Conftest policy-as-code (require-image-digest-pin, `readOnlyRootFilesystem`, drop ALL caps) runs in audit mode

## Documentation

- [Platform description (FR)](docs/DevOps_Central_Platform_Description.md)
- [Implementation steps (FR)](docs/DevOps_Central_Platform_Etapes_Implementation.md)
- [Runbook index](docs/runbook-index.md) · [Pod crash-loop](docs/runbook-pod-crashloop.md) · [Vault sealed](docs/runbook-vault-sealed.md)
- [Disaster recovery](docs/disaster-recovery.md) · [SLOs](docs/slo.md)

## Configure your repo identity

Manifests reference images and repositories via `<owner>`/`<repo>` placeholders. Replace them with your own GitHub owner and repository before deploying:

```bash
grep -rn "<owner>\|<repo>" k8s/ app/ docs/ --include="*.yaml" --include="*.md" --include="Dockerfile"
```

Affected files: `k8s/apps/**/kustomization.yaml` (GHCR image paths), `k8s/argocd/**` (repoURLs), `app/*/Dockerfile` (OCI source label), `docs/comprendre-le-projet.md`.

## License

[MIT](LICENSE)
