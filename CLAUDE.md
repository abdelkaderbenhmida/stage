# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See also `AGENTS.md` in the repo root — it has the same audience and should be kept in sync with this file.

## What this is

DevOps Central Platform: a homelab (libvirt/KVM) platform combining Terraform (VM provisioning) + Ansible (kubeadm cluster config) + 3 FastAPI microservices + Kubernetes + ArgoCD (GitOps) + observability (Prometheus/Grafana/ELK/AlertManager). Specs are in French: `docs/DevOps_Central_Platform_*.md`.

## Commands

**Tests** (mirrors what CI runs):
```bash
ENVIRONMENT=dev LOG_FORMAT=plain VAULT_ADDR="" pytest -q tests/ -v
```
Run a single test: `pytest -q tests/test_services.py::test_name -v`

**Lint**:
```bash
ruff check app/                       # Python
scripts/validate-platform.sh --ci     # 7-check platform validator, gating
terraform fmt -check -recursive terraform/
yamllint k8s/
```

**Pre-commit** (ruff, yamllint, gitleaks, terraform fmt/validate): `pip install pre-commit && pre-commit install && pre-commit run --all-files`

**Build a service image** — context is always `app/`, never the service subdir, because services `import shared.`:
```bash
docker build -t "<svc>:1.0.0" -f "app/<svc>/Dockerfile" app/
```

**Run a service locally** — `main.py` calls `get_secret()`, which fails closed without Vault, so always set `ENVIRONMENT`:
```bash
cd app/<svc> && PYTHONPATH=../shared:. ENVIRONMENT=dev python -c "import main"
```

**Infra provisioning** (destructive/stateful — confirm before running):
```bash
cd terraform && terraform init && terraform apply
scripts/generate-inventory.sh              # TF state -> ansible/inventory.ini (--no-refresh skips TF refresh)
cd ../ansible && ansible-playbook playbook.yml
# cluster wipe is opt-in and requires BOTH flags:
ansible-playbook playbook.yml --tags reset -e reset_confirmed=true
```

**Validation scripts**:
- `scripts/validate-platform.sh` — 7 checks + self-heal/rollback (destructive). Flags: `--ci`, `--skip-incident`, `--only 1,2,5`, `--namespace`, `--image-tag`.
- `scripts/validate-security.sh` — gitleaks / trivy / vault / token checks.

## Architecture

```
GitHub Actions CI (lint -> gitleaks -> test -> build -> trivy-scan -> deploy)
  builds 3 images on GHCR, digest-pinned
        |
K8s cluster (homelab libvirt/KVM, 1 master + 2 workers, containerd + Calico)
  users-svc / products-svc / orders-svc (FastAPI :8000, HPA, PDB, NetworkPolicies)
  ArgoCD (prune + selfHeal, reconciles everything from git)
  Vault (fail-closed secrets, KV v2 per-service)
  Prometheus (15s scrape) -> Grafana (4 dashboards) -> AlertManager (SLO rules)
  Filebeat -> Logstash -> Elasticsearch -> Kibana
```

- `terraform/` — provisions the libvirt/KVM VMs via the `dmacvicar/libvirt` provider, pinned `~> 1.5` (do not bump to 1.6+). Renders `terraform/inventory.generated.ini` from `inventory.tpl`; that's the only file `scripts/generate-inventory.sh` reads, and it copies it to `ansible/inventory.ini` — **never hand-edit `ansible/inventory.ini`**. State is local (no remote backend); `.tfstate`/`.tfvars` are gitignored (a past leak of a state file with a cleartext SSH key is why — see `.gitignore` header).
- `ansible/playbook.yml` — 5 plays in order: `docker` role (containerd/Docker) -> `k8s_common` (kubeadm prereqs) -> `k8s_master` (`kubeadm init` + Calico v3.26.1) -> `k8s_worker` (`kubeadm join`, `serial: 1`) -> `k8s_reset` (opt-in, gated by `--tags reset -e reset_confirmed=true`). Pinned versions live in `ansible/group_vars/all.yml` (k8s 1.28, Calico v3.26.1, pod CIDR `192.168.0.0/16`).
- `app/` — 3 near-identical FastAPI CRUD services (`users-service`, `products-service`, `orders-service`), each exposing `GET /<resource>`, `/livez`, `/readyz`, `/metrics`. They share `app/shared/`: `vault_client.py` (`get_secret(name, default=None)`, resolution order Vault -> env var -> default -> raise `SecretUnavailable`, fails closed in production; `ENVIRONMENT=dev` allows sqlite/env fallback), `log_config.py` (structured JSON logs consumed by Filebeat/ELK), `config.py` (`AppConfig`). `shared/` is imported as a top-level package (`import shared.`), which is why the Docker build context and `PYTHONPATH` tricks above matter.
- `k8s/apps/` — kustomize: `base/` (namespace, hpa, pdb, rbac, networkpolicies) + per-service dirs (`users/`, `products/`, `orders/`) + `overlays/{dev,staging,prod}`. Root `kustomization.yaml` aggregates everything and pins images to `ghcr.io/<owner>/<svc>`.
- `k8s/argocd/applications/` — one Application CRD per workload, `prune: true` + `selfHeal: true`; after the one-time `kubectl apply -k k8s/argocd/install/`, ArgoCD owns further syncs — don't `kubectl apply` workload manifests directly once this is running.
- `k8s/vault/` — Vault dev mode; root token injected out-of-band by `scripts/bootstrap-vault-secret.sh`, never committed. `vault-policy.hcl` gives each service `read` on its own `secret/data/devops-platform/<svc>` path only.
- `k8s/monitoring/` — Prometheus (`serviceMonitorSelector: {}`, picks up all ServiceMonitors), Grafana (4 dashboards, datasources for Prometheus + Elasticsearch), AlertManager (`k8s/monitoring/alertmanager/rules.yaml` — SLO recording rules), ELK.
- `k8s/policies/conftest/` — policy-as-code (image digest pin, `readOnlyRootFilesystem`, drop-ALL-caps), run in **audit mode** (non-blocking) by CI.
- `.github/workflows/ci-cd.yml` — 8 jobs: `lint`, `gitleaks`, `terraform-validate` run in parallel; `test` needs `[lint, gitleaks]`; `build` needs `test`; `trivy-scan` needs `build`; `deploy` needs `[trivy-scan, terraform-validate]` and only runs on `workflow_dispatch`; `load-test` only on `schedule`. `fail-fast: false`, `concurrency: cancel-in-progress`. Triggers only when paths under `app/`, `k8s/`, `terraform/`, `ansible/`, `scripts/`, `.gitleaks.toml`, or the workflow file change — docs/tests-only changes skip CI.

## Gotchas that matter

- **Docker build context is `app/`, not the per-service dir.** Always `docker build -f app/<svc>/Dockerfile app/`. Images must run as non-root with a `HEALTHCHECK` (enforced by conftest + liveness/readiness probes).
- **Never hand-edit `ansible/inventory.ini`** — it's generated by `scripts/generate-inventory.sh` from Terraform state via `inventory.tpl`.
- **`get_secret()` fails closed.** Don't run a service's `main.py` without `ENVIRONMENT` set — in non-dev environments it exits rather than start without a resolvable Vault secret.
- **Trivy and gitleaks are both blocking in CI** (`--exit-code 1` / findings fail the pipeline); conftest is audit-mode only (non-blocking).
- **No mutable `:latest` outside the default branch.** Images tag as `branch-name` + `commit-<sha>`; Trivy scans the build **digest**, not a tag, to avoid races between matrix jobs.
- **Terraform is pinned `~> 1.5`** in `terraform/main.tf` / CI matrix (`1.5.7`) — 1.6+ is untested, don't bump it.
- K8s manifest linting: kubeconform excludes Helm `values.yaml` / `Chart.yaml` / `crds.yaml` — replicate that filter if you write your own `find`/lint invocations.
- `archive/` is locally kept but gitignored — not part of the project, ignore it.
- `tests/k6/load-test.js` is referenced by the nightly `load-test` CI job (`BASE_URL` env-driven, defaults to the `users-service` ClusterIP).
