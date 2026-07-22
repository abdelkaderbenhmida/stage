# DevOps Central Platform — Agent Notes

3 FastAPI microservices (users/products/orders) backed by Vault, deployed to
K8s via Terraform → Ansible → kubectl (+ ArgoCD). Repository for a DevOps
staging project. Read `README.md` first for architecture prose.

## Source-of-truth pointers

- Audit + remediation history: see `findings.md` / `RAPPORT_*.md` (French);
  remediation changelog authored against `devops-analysis-report.md` (kept out
  of `.dockerignore`, not in repo — referenced by comments only).
- Spec: `docs/DevOps_Central_Platform_Description.md` + `Etapes_Implementation.md`.
- Phase 7 validation contract: `files.md/DevOps_Central_Platform_Etapes_Implementation.md`.
- OpenCode plugin config: `.opencode/opencode.json` (only loads graphify.js plugin).
- No `opencode.json` `instructions:` field — this file is the sole agent guide.

## Critical: Docker build context

Always `app/`, NOT the service dir. Per-service Dockerfiles `COPY shared/` and
`pip install ./shared` from a sibling dir.

```bash
docker build -t users-service:1.0.0 -f app/users-service/Dockerfile app/
```

Wrong context breaks every build silently — Docker layer cache hides missing
`shared/`. CI's build job (`context: app/`) is the canonical copy.

## Python layout gotchas

- `app/shared/` is a real package (`pyproject.toml` → `name=devops-platform-shared`,
  `package-dir: shared="."`). Import as `from shared.vault_client import ...`.
  Installed via `pip install -e app/shared/` in CI test job; containers `pip
  install ./shared` in the builder stage then copy site-packages.
- Image sets `PYTHONPATH=/app` AND site-packages already has `shared` egg —
  both paths work. Don't rely on `app/<svc>/` being on `PYTHONPATH`; the service
  imports from `shared.*`, not from a sibling file.
- Stale dead code: `app/{users,products,orders}-service/vault_client.py` are
  leftover per-service copies. **Do not import or edit them.** Services only
  use `shared.vault_client`. Safe to delete in a cleanup pass, but not required.
- Requires Python >=3.11 (pyproject pin). CI uses 3.11.

## Fail-closed secrets contract (do not break)

`app/shared/vault_client.py` raises `SecretUnavailable` instead of returning `{}`.
Each service `main.py` runs `_load_secrets()` at startup. Behavior:

- `ENVIRONMENT` in `{dev, development, local}` → dev fallbacks allowed (ephemeral
  JWT, sqlite). Other values OR unset → `ENVIRONMENT=production` semantics.
- Missing required secret in prod → `raise SystemExit(...)`. Never add a silent
  fallback "just to make it boot".
- `get_secret(name, default=...)` — only pass `default` for non-sensitive dev
  values (logged as `secret.default_used`).
- Tests run with `ENVIRONMENT=dev VAULT_ADDR=""` precisely to exercise the
  env-override branch. Keep that pattern when adding tests.

## Lint / format / validate

```bash
# Python — ruff is the only linter.
pip install ruff==0.1.9 yamllint==1.33.0
ruff check app/shared/ app/users-service/ app/products-service/ app/orders-service/

# Terraform
cd terraform && terraform fmt -check -recursive . && terraform validate

# YAML manifests (line-length off — many long lines are intentional)
yamllint -d "{rules: {line-length: disable}}" k8s/

# K8s schema (exclude Helm values.yaml / crds.yaml — not standalone manifests)
kubeconform -strict -ignore-missing-schemas -kubernetes-version 1.28.0 \
  -schema-location 'https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone/{{.ResourceKind}}.json' \
  $(find k8s/ -name '*.yaml' ! -name 'values.yaml' ! -name 'crds.yaml')
```

CI lint job runs all four. Match versions (ruff 0.1.9, yamllint 1.33.0, kubeconform
v0.6.4, terraform 1.5.7) or the gate diverges.

## Tests / dep audit

```bash
pip install pytest httpx pip-audit
pip install -e app/shared/
for svc in users-service products-service orders-service; do
  pip install -r app/$svc/requirements.txt
done

# pip-audit — strict, blocks on any known-vulnerable pinned dep
pip-audit -r app/users-service/requirements.txt \
          -r app/products-service/requirements.txt \
          -r app/orders-service/requirements.txt --strict

# shared package imports
python -c "from shared.vault_client import get_secret; from shared.log_config import setup_logging; from shared.config import AppConfig; print('ok')"

# Per-service import smoke — must run from inside service dir with PYTHONPATH trick
for svc in users-service products-service orders-service; do
  (cd app/$svc && PYTHONPATH=../shared:. ENVIRONMENT=dev python -c "import main")
done

# pytest smoke (currently `|| true` in CI — kept because no real tests exist yet)
ENVIRONMENT=dev LOG_FORMAT=plain VAULT_ADDR="" pytest -q app/
```

No real unit tests yet — smoke-import is the gate. Adding tests: put under
`app/<svc>/tests/` or a top-level `tests/` with `httpx` + FastAPI TestClient
(see Pending below).

## CI/CD contract (`.github/workflows/ci-cd.yml`)

```
lint └→ gitleaks └→ test └→ build (matrix) └→ trivy-scan (matrix, digest-pinned)
                 └→ terraform-validate
deploy: workflow_dispatch only + protected `production` environment
```

Non-obvious rules:

- `permissions: contents: read` default; jobs escalate explicitly. Don't add a
  job without declaring its permissions.
- `concurrency: cancel-in-progress: true` per `workflow-ref`. Avoid pushing to
  the same branch repeatedly during dev — cancels in-flight runs.
- Build job outputs `${{ steps.build.outputs.digest }}` → trivy-scan pulls
  `ghcr.io/<owner>/<svc>@sha256:<digest>`. **Never scan `:latest` in CI** —
  the race is the bug that motivated the digest fix (P1).
- Default branch pushes emit a `:latest` tag (`enable={{is_default_branch}}`);
  other branches only get `branch-<name>` + `commit-<sha>`. Trivy scan
  without digest only works because build→trivy is a job dependency chain.
- Nightly schedule cron `0 3 * * *` — catches new CVEs in pinned deps.
- `paths:` filter means edits to `docs/` alone do NOT trigger CI. If you
  change a path that CI depends on (e.g. `.gitleaks.toml` is in the filter),
  the workflow ref itself is also in the filter list.

## Deploy flow (local / homelab)

```bash
# 1. Infra
cd terraform && terraform init && terraform apply
scripts/generate-inventory.sh          # regenerates ansible/inventory.ini

# 2. Provision cluster
cd ../ansible
ansible-galaxy collection install -r requirements.yml   # needed: community.general
ansible-playbook playbook.yml

# 3. K8s manifests (manual) — ArgoCD syncs after bootstrap
kubectl apply -f k8s/apps/base/namespace.yaml
kubectl apply -f k8s/vault/manifests.yaml
scripts/bootstrap-vault-secret.sh       # injects root token via stdin → kubectl
scripts/bootstrap-elasticsearch-secret.sh
kubectl apply -f k8s/apps/
kubectl apply -k k8s/argocd/install/     # one-time ArgoCD bootstrap
```

Order matters: `namespace.yaml` first (LimitRange+ResourceQuota), Vault before
bootstrap-vault-secret before apps (apps `optional: false` secretKeyRef —
fail-closed if Vault/secret not yet created).

## Ansible reset — opt-in ONLY

```bash
ansible-playbook playbook.yml --tags reset -e reset_confirmed=true
```

The reset play needs BOTH `--tags reset` (skipped by default via the `never`
tag) AND `-e reset_confirmed=true`. Don't remove either gate. A bare
`ansible-playbook playbook.yml` will NOT reset.

## Vault secrets bootstrap

`scripts/bootstrap-vault-secret.sh` pipes the root token via stdin → kubectl,
so the token never lands on disk or in `ps`. The placeholder Secret manifest
in `k8s/` is explicit-no-token (`INJECT-VIA...`). Don't hardcode a token in
the manifest.

`scripts/validate-security.sh` check 4 actually logs in to Vault with the
secret value — verifying the token is live, not just present. Don't replace
with a `kubectl get secret` no-op check.

## Validation scripts

```bash
scripts/validate-platform.sh                    # colored summary, all 7 checks + self-heal + rollback
scripts/validate-platform.sh --ci               # gating, exit 1 on failure
scripts/validate-platform.sh --skip-incident    # skip destructive self-heal/rollback tests
scripts/validate-platform.sh --only 1,2,5       # subset
scripts/validate-security.sh                    # 4 security checks (gitleaks/trivy/vault/token)
scripts/validate-security.sh --ci               # gating
```

Both scripts: missing tool in local mode = SKIP (yellow); in `--ci` mode =
FAIL and exit 1. Don't downgrade `--ci` failures to skips. Both use `jq`,
not python3, for JSON parsing — keep that.

## Terraform

- `required_version ~> 1.5`, `libvirt ~> 0.9` (KVM homelab provider). Cloud
  migration: swap `provider "libvirt"` block, don't fork the rest.
- Backend is `local` by default (single-user homelab). `backend.tf` has a
  commented S3 block — uncomment + migrate with `terraform init` for any team
  / CI use. `terraform.tfstate` is gitignored (P0 #4 — a previous commit leaked
  an SSH pub key via state).
- `ssh_public_key` var is `sensitive = true`; all 5 outputs are sensitive.
  Don't unsensitive them.
- `gateway_ip` derives from `cidrhost(var.network_cidr, 1)` when not set.
  Don't re-introduce a hardcoded default IP.
- Pinned linter version: terraform 1.5.7 in CI.

## Gitleaks + secrets in repo

- `.gitleaks.toml` allowlist is intentionally narrow. Do NOT add `.*\.md$` (masks
  every markdown doc) or `changeme` (matches the `change-me` token) — both
  were removed as P2 hardening.
- `files.md/*.txt` IS allowlisted (verbatim reference snippets only). Adding
  new files there requires per-file review.
- Never commit `.env`, `ansible/.vault-pass`, `*.tfstate`, `*.tfvars`.
  `terraform.tfvars.example` is the one exception (gitignored pattern negation).

## K8s manifest conventions

- Every container: `readOnlyRootFilesystem: true`, `seccompProfile:
  RuntimeDefault`, explicit `runAsUser`, `drop: ALL` cap. Don't add a
  container without all four.
- Probes split: `/livez` (process alive, no deps) vs `/readyz` (checks Vault
  via `vault_health()`). Don't merge them — the split is the fix for
  silent-secret-fallback degradation.
- ResourceQuota + LimitRange in `k8s/apps/base/namespace.yaml` must be applied
  before workloads.
- App image pins are semver (`users-service:1.0.0`) in manifests, but CI build
  emits `:latest` + `:branch` + `commit-<sha>` tags. Production deploy should
  move to `@sha256:<digest>` pins (see Pending).

## Logging format

`LOG_FORMAT=json` in containers (via Dockerfile `ENV`); `LOG_FORMAT=plain`
for local dev. `shared/log_config.py` switches on it. Structured logs go to
ELK via Filebeat — keep `extra={"event": ...}` style or dashboards break.

## Pending follow-up (don't re-introduce these as fixes)

- Wire Vault Agent Injector annotations in each Deployment; drop `VAULT_TOKEN`
  env. K8s auth method is configured by the setup Job — only the consumer side
  pending.
- Replace `ghcr.io/.../<svc>:1.0.0` manifest pins with `@sha256:<digest>`
  from `steps.build.outputs.digest`.
- Add `terraform.tfvars.example` + per-env override docs (currently blocked
  only by absence of the example file; `*.tfvars` is gitignored).
- Add real pytest unit tests per service (smoke-import is currently the gate).
- Vault runs in dev mode — switch to raft storage + auto-unseal for prod.

## Branch / commit notes

- Active branch: `clean-main` (not `main`). `main`/`develop` are the CI
  trigger branches — PRs to either run the workflow.
- Commit style observed: `feat(phase<N>): ...`, `feat: ...`. Match when
  extending a phase.
