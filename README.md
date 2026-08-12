# DevOps Central Platform

[![CI/CD](https://github.com/<owner>/<repo>/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/<owner>/<repo>/actions/workflows/ci-cd.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

End-to-end DevOps platform built around **3 FastAPI microservices** (Users, Products, Orders). Demonstrates the full modern DevOps toolchain: Infrastructure as Code, automated configuration, containerization, Kubernetes orchestration, DevSecOps pipeline hardening, GitOps, and complete observability (metrics + centralized logs).

> Full specification (French): [`docs/DevOps_Central_Platform_Description.md`](docs/DevOps_Central_Platform_Description.md) and [`docs/DevOps_Central_Platform_Etapes_Implementation.md`](docs/DevOps_Central_Platform_Etapes_Implementation.md)

> **Note:** Before deploying, replace the `<owner>`/`<repo>` placeholders in the manifests (see [Configure your repo identity](#configure-your-repo-identity)).

---

## Stack

| Layer | Tool |
|---|---|
| Infrastructure as Code | Terraform (libvirt/KVM homelab provider or equivalent cloud) |
| Configuration | Ansible (roles `docker`, `k8s_common`, `k8s_master`, `k8s_worker`) |
| Containerization | Docker multi-stage builds, non-root images + HEALTHCHECK |
| Orchestration | Kubernetes + Helm (Prometheus, Grafana, ELK charts) |
| Security | Trivy (image scanning), Gitleaks (secret scanning), HashiCorp Vault (dynamic secrets) |
| GitOps | GitHub Actions (CI), ArgoCD (sync) |
| Observability | Prometheus (15s metrics), Grafana (3 dashboards), ELK Stack (logs), AlertManager (SLO rules) |

## Architecture

```
Terraform ──provisions──▶ 3 VMs (1 master + 2 workers, libvirt/KVM)
   │
Ansible ──configures──▶ Docker + Kubernetes 1.28 (containerd, Calico)
   │
GitHub Actions CI ──builds & scans──▶ images on GHCR (Trivy, Gitleaks)
   │
ArgoCD ──GitOps sync──▶ k8s/apps (3 services) + monitoring stack
   │
Prometheus / Grafana / ELK / AlertManager ──observability──▶ SLO dashboards
```

## Repository layout

```
├── terraform/                # IaC (main, variables, outputs, backend, inventory.tpl)
├── ansible/                  # Roles docker + k8s_*
├── app/                      # Services auto-discovered by main.py presence
│   ├── Dockerfile            # ONE generic Dockerfile (SERVICE_NAME build-arg)
│   ├── users-service/  products-service/  orders-service/
│   └── shared/               # vault_client, log_config, config
├── k8s/
│   ├── apps/
│   │   ├── base/                # Namespace + NetworkPolicies (static)
│   │   └── chart/               # Generic Helm chart — one Deployment/Service/
│   │                            #   SA/HPA/PDB per discovered service
│   ├── monitoring/           # Prometheus, Grafana, ELK, AlertManager
│   ├── argocd/               # ApplicationSet + Applications + install
│   └── vault/                # Vault manifests + policy.hcl
├── scripts/                  # validate-platform.sh, validate-security.sh, bootstrap-*, generate-inventory.sh
├── .github/workflows/ci-cd.yml  # lint → gitleaks → test → build → trivy → deploy
└── docs/                     # Specification and runbooks
```

## Requirements

- `git`, `terraform` (~> 1.5), `ansible`, `docker`, `kubectl`, `helm`, `jq`
- Homelab: 8 GB RAM + 4 vCPU per node (libvirt/KVM), or an equivalent AWS/GCP/Azure setup

## Quick start

### Build images (one generic Dockerfile, SERVICE_NAME build-arg)

```bash
# app/Dockerfile is the ONLY Dockerfile — the service dir name is injected.
for svc in users-service products-service orders-service; do
  docker build -t "${svc}:1.0.0" --build-arg SERVICE_NAME=$svc -f app/Dockerfile app/
done
# Or build whatever is discovered, automatically:
for d in app/*/; do
  [ -f "$d/main.py" ] && docker build -t "$(basename "$d"):1.0.0" --build-arg SERVICE_NAME="$(basename "$d")" -f app/Dockerfile app/
done
```

### Provision infra → configure → deploy

```bash
cd terraform && terraform init && terraform apply
scripts/generate-inventory.sh        # regenerates ansible/inventory.ini (never hand-edit)
cd ../ansible && ansible-playbook playbook.yml
```

### Deploy platform on the cluster

```bash
# 1. Static base (namespace, network policies)
kubectl apply -k k8s/apps/base/

# 2. Vault (secrets are injected out-of-band, fail-closed)
kubectl apply -f k8s/vault/manifests.yaml
scripts/bootstrap-vault-secret.sh

# 3. App services — discovery-driven Helm chart.
#    ANY directory under app/ with a main.py is a service:
#    no names are hardcoded anywhere.
for svc in app/*/; do
  [ -f "$svc/main.py" ] && printf '  - name: %s\n' "$(basename "$svc")"
done > /tmp/services.yaml
helm template apps k8s/apps/chart -f <(printf 'services:\n'; cat /tmp/services.yaml) | kubectl apply --server-side -f -

# 4. Keyed provisioning: fresh services get empty placeholder keys —
#    write real secrets with:
#      vault kv put secret/devops-platform/<service> <KEY>=<value>

# 5. GitOps bootstrap (once), then ArgoCD auto-syncs: one Application per
#    discovered service (see k8s/argocd/applicationset.yaml)
kubectl apply -k k8s/argocd/install/

### Validation

```bash
scripts/validate-platform.sh                 # summary
scripts/validate-platform.sh --ci            # gating (exit 1 on failure)
scripts/validate-platform.sh --skip-incident # skip destructive self-heal/rollback
scripts/validate-security.sh                 # gitleaks / trivy / vault / token checks
```

## CI/CD pipeline

`.github/workflows/ci-cd.yml`:

```
lint → gitleaks → test → build → trivy-scan → deploy (workflow_dispatch only)
```

Hardening: `fail-fast: false`, `concurrency: cancel-in-progress`, least-privilege `permissions`, `paths` filters, Trivy `--severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed` + SBOM + SARIF, no mutable `:latest` tags outside the default branch.

## SLOs

| SLI | SLO |
|---|---|
| Availability | ≥ 99.9% over 30 days |
| Latency P95 | < 200 ms |
| 5xx error rate | < 1% |
| MTTD | < 2 min |
| MTTR | < 30 min |

Rules implemented in [`k8s/monitoring/alertmanager/rules.yaml`](k8s/monitoring/alertmanager/rules.yaml).

## Configure your repo identity

The manifests reference images and repositories via placeholders. Before deploying, replace them with your own GitHub owner and repository name:

```bash
grep -rn "<owner>\|<repo>" k8s/ app/ docs/ --include="*.yaml" --include="*.md" --include="Dockerfile"
```

Affected files: `k8s/argocd/**` (repoURLs), `app/Dockerfile` (OCI source label), `docs/comprendre-le-projet.md`.

## Security

- `terraform.tfstate` and `*.tfvars` are gitignored and never committed
- Gitleaks scans for secrets (local pre-commit + blocking CI job)
- Trivy blocks the pipeline on CRITICAL/HIGH vulnerabilities
- `pip-audit --strict` fails the pipeline on known-vulnerable pinned dependencies
- Vault secrets fail closed: services refuse to start without a resolvable secret (dev fallback via `ENVIRONMENT=dev`)

## License

[MIT](LICENSE)
