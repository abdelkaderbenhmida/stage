# AGENTS.md

DevOps Central Platform — homelab (libvirt/KVM) platform using Terraform + Ansible + 3 FastAPI microservices + Kubernetes + ArgoCD + observability. GitOps CI in `.github/workflows/ci-cd.yml`. Specs are in French: `docs/DevOps_Central_Platform_*.md`.

## Repo layout
- `app/` — 3 FastAPI services (`users-service`, `products-service`, `orders-service`) + `shared/` lib (Vault client, log_config, config).
- `terraform/`, `ansible/`, `k8s/` (apps → base/overlays/dev/staging/prod), `scripts/`, `docs/`, `tests/`.
- `archive/` — locally kept but gitignored; not part of the project.
- Active branch is `remove/canary-pipeline` (Flagger/Istio/canary removed). Other branches: `main`, `phase6-observability`.

## Commands — local verification
- Secrets/lint/test all run inside CI under `ENVIRONMENT=dev` so Vault is skipped. Reproduce locally:
  ```
  ENVIRONMENT=dev LOG_FORMAT=plain VAULT_ADDR="" pytest -q tests/ -v
  ENVIRONMENT=dev LOG_FORMAT=plain pytest -q tests/ -v
  ```
- Lint: `ruff check app/` (all services) then `scripts/validate-platform.sh --ci`.
- Pre-commit hooks: `pip install pre-commit && pre-commit install`; run `pre-commit run --all-files`. Covers ruff, yamllint, gitleaks, terraform fmt/validate.

## Gotchas that matter
- **Docker build context is `app/`, not per-service.** Always `-f app/<svc>/Dockerfile app/`. App must run as non-root with HEALTHCHECK.
- **FastAPI env-driven secrets**: `get_secret()` fails closed in production (exits if no Vault secret). `ENVIRONMENT=dev` falls back to sqlite memory / ephemeral token. Don't run services' `main.py` without `ENVIRONMENT` set.
- `shared/` is imported as top-level package; import pattern `(cd app/$svc && PYTHONPATH=../shared:. python -c "import main")`.
- **`terraform.tfstate` must never be committed** — gitignored and whitelisted in `.gitleaks.toml` only as defense-in-depth. `*.tfvars` gitignored too.
- K8s YAML validation: kubeconform excludes Helm `values.yaml`/`crds.yaml` via `! -name` filters; conftest policy-as-code runs in audit mode (non-blocking). Replicate those `find` filters when linting manifests.
- Trivy scan is blocking on CRITICAL/HIGH (`--exit-code 1`); gitleaks scan is blocking too.

## Validation scripts
- `scripts/validate-platform.sh` — 7 checks, RHEL self-heal + rollback (destructive). Flags: `--ci` (exit 1 on fail), `--skip-incident`, `--only 1,2,5`, `--namespace`, `--image-tag`.
- `scripts/validate-security.sh` — 4 security checks (gitleaks/trivy/vault/token).
- `scripts/generate-inventory.sh` — terraform refresh + `-target local_file.ansible_inventory`, then copies to `ansible/inventory.ini` (must be regenerated, never hand-edited).

## Push/gating
- CI order: `lint → gitleaks → test → build → trivy-scan`, `fail-fast: false`, `concurrency: cancel-in-progress`, `workflow_dispatch`-only for prod deploy.