# AGENTS.md

DevOps Central Platform — homelab (libvirt/KVM) platform using Terraform + Ansible + 3 FastAPI microservices + Kubernetes + ArgoCD + observability. GitOps CI in `.github/workflows/ci-cd.yml`. Specs are in French: `docs/DevOps_Central_Platform_*.md`.

## Repo layout
- `app/` — 3 FastAPI services (`users-service`, `products-service`, `orders-service`) + `shared/` lib (Vault client, log_config, config).
- `terraform/`, `ansible/`, `k8s/` (apps → per-service kustomize dirs `users/`/`products/`/`orders/` + `base/` + `overlays/dev/staging/prod`; also `monitoring/`, `argocd/`, `vault/`, `policies/`), `scripts/`, `docs/`, `tests/`.
- `archive/` — locally kept but gitignored; not part of the project.
- Active branch is `secondary` (also triggers CI). Others: `main`, `remove/canary-pipeline` (Flagger/Istio/canary removed), `phase6-observability`, `feat/observability-hardening`. CI pushes run on `main`, `develop`, `clean-main`, `secondary`.

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
- K8s YAML validation: kubeconform excludes Helm `values.yaml`/`Chart.yaml`/`crds.yaml` via `! -name` filters; conftest policy-as-code runs in audit mode (non-blocking). Replicate those `find` filters when linting manifests.
- Trivy scan is blocking on CRITICAL/HIGH (`--exit-code 1`); gitleaks scan is blocking too.

## Validation scripts
- `scripts/validate-platform.sh` — 7 checks, RHEL self-heal + rollback (destructive). Flags: `--ci` (exit 1 on fail), `--skip-incident`, `--only 1,2,5`, `--namespace`, `--image-tag`.
- `scripts/validate-security.sh` — 4 security checks (gitleaks/trivy/vault/token).
- `scripts/generate-inventory.sh` — terraform refresh + `-target local_file.ansible_inventory`, then copies to `ansible/inventory.ini` (must be regenerated, never hand-edited). `--no-refresh` skips terraform refresh.
- CI test job also runs `pip-audit --strict` on all requirements.txt — adding a pinned dep with a known vuln fails the pipeline.

## Push/gating
- CI order: `lint → gitleaks → test → build → trivy-scan → deploy`, `fail-fast: false`, `concurrency: cancel-in-progress`, `workflow_dispatch`-only for prod deploy.
- CI triggers only on `paths` in `.github/workflows/ci-cd.yml` (`app/`, `k8s/`, `terraform/`, `ansible/`, `scripts/`, `.gitleaks.toml`, workflow itself) — docs/tests-only changes skip CI.
- Terraform pinned `~> 1.5` (`required_version` in `terraform/main.tf`, CI matrix 1.5.7). Do not bump to 1.6+.
- Images tagged branch-name + `commit-<sha>`; no mutable `:latest` outside default branch. Trivy scans by tag from `github.ref_name` — matrix rows must not race on the same tag.
- Nightly `schedule` jobs: drift scan (pip-audit style re-check) + k6 load test (`tests/k6/load-test.js`, `BASE_URL` env-driven, defaults to `users-service` ClusterIP).