# DevOps Central Platform — DevOpS Engineering Notes

This file documents the remediation work performed against the findings
in `devops-analysis-report.md`. See that file for the original audit + P0/P1/P2
finding IDs.

## Remediation map (finding → fix)

| Finding ID | Fix applied |
|---|---|
| P0 #1 | Vault root token removed from all 5 git-tracked locations; `scripts/bootstrap-vault-secret.sh` injects it out-of-band via stdin → kubectl; placeholder Secret file is explicit-no-token; `optional: false` on app secretKeyRef so pods fail-closed. |
| P0 #2 | DB credentials removed from the `vault-setup` ConfigMap script and merged into Vault KV paths only; setup.sh reads them via env when seeding (dev only). |
| P0 #3 | `app/shared/vault_client.py` rewritten to raise `SecretUnavailable` instead of silently returning `{}`; each service `main.py` runs `_load_secrets()` at startup and exits if `ENVIRONMENT != dev` and a secret is missing. |
| P0 #4 | tfstate + generated inventory removed from git tracking; `.gitignore` blocks all `*.tfstate*`; `backend.tf` documents S3 backend migration; `ssh_public_key` variable marked `sensitive = true`; all 5 outputs marked sensitive. |
| P0 #5 | CI test job drops `\|\| true`; adds `pip-audit`, real `pytest` smoke import per service, `pip install -e app/shared/` so the shared wheel is the import path tested. |
| P0 #6 | `validate-security.sh` rewritten: `set -euo pipefail`, fail-not-skip in `--ci`, drops the `echo ok` no-op, uses `jq` not python3, scans `:latest` matching CI build tag. |
| P1 | Semgrep SAST job in CI (SARIF uploaded); `paths:` filters + `concurrency:` cancel-in-progress; `permissions: contents: read` default + per-job escalation; `fail-fast: false`; deploy stage behind `workflow_dispatch` + protected `production` environment; pinned app image semver (`ghcr.io/.../users-service:1.0.0`); NetworkPolicies + PDBs + topologySpread + podAntiAffinity; cloud-init hardened (`ssh_pwauth:false`, `disable_root`, fail2ban, restricted sudoers via `sudo:` line); per-service SAs in `rbac.yaml`; `vault-sa` mounted on both the Vault Deployment and the setup Job; `automountServiceAccountToken: true` so future Kubernetes auth works; `app/shared/` made a real Python package (`pyproject.toml`, `__init__.py`); per-service `vault_client.py` copies deleted. |
| P2 | `.dockerignore` prevents shipping `.git`/`__pycache__`; secrets in Vault KV only; gitleaks allowlist tightened (`.*\.md$` removed, `changeme` removed); Dependabot config for pip/docker/github-actions; tf `validation` blocks on `network_cidr`/`worker_count`/`vm_vcpu`/etc.; `gateway_ip` derived from `cidrhost(var.network_cidr, 1)`; `dns` forwarders wired to `var.dns_servers`; TF lock constraint `~> 0.7` corrected to `~> 0.9`; UID/GID hardcoded `64055/993` replaced with `var.libvirt_volume_owner_uid/gid`; `readOnlyRootFilesystem`+`seccompProfile RuntimeDefault`+explicit `runAsUser`+`drop: ALL` on every container; `/livez` vs `/readyz` split (readyz checks Vault); structured JSON logging via `shared/logging.py` + `python-json-logger`; per-role `defaults/main.yml` + `meta/main.yml` (Galaxy-compliant); Ansible `requirements.yml` declares `community.general`; `k8s_reset` play now gated by `when: reset_confirmed \| bool` + `never` tag; kubeadm join txt mode `0644 → 0600`; kubeconform + yamllint run in CI lint job. |

## How to verify after the changes

### Lint / format
```bash
# Python
pip install ruff yamllint
ruff check app/shared/ app/users-service/ app/products-service/ app/orders-service/

# Terraform
cd terraform && terraform fmt -check -recursive .

# YAML manifests
yamllint -d "{rules: {line-length: disable}}" k8s/

# K8s schema validation
kubeconform -kubernetes-version 1.28.0 k8s/
```

### Security checks (local)
```bash
scripts/bootstrap-vault-secret.sh   # create/rotate the dev root token Secret
scripts/validate-security.sh        # run all 4 checks; add --ci for gating
```

### Build
```bash
# context MUST be app/ so app/shared is visible to the COPY commands.
for svc in users-service products-service orders-service; do
  docker build -t "$svc:1.0.0" -f "app/$svc/Dockerfile" app/
done
```

### Deploy
```bash
kubectl apply -f k8s/apps/namespace.yaml      # Namespace + LimitRange + ResourceQuota
kubectl apply -f k8s/vault/manifests.yaml
scripts/bootstrap-vault-secret.sh             # creates vault-root-token Secret
kubectl apply -f k8s/apps/
# rollout watches
kubectl rollout status deploy/vault -n vault
kubectl rollout status -n devops-platform deploy/users-service deploy/products-service deploy/orders-service
```

### Run Ansible
```bash
cd ansible
ansible-galaxy collection install -r requirements.yml
# bootstrap
ansible-playbook playbook.yml
# reset — explicitly opt-in
ansible-playbook playbook.yml --tags reset -e reset_confirmed=true
```

## Pending follow-up (not blocking this pass — production hardening)

- Wire Vault Agent Injector annotations in each Deployment and drop the
  `VAULT_TOKEN` env. The Kubernetes auth method is already configured by the
  setup Job; only the consumer side remains.
- Replace `ghcr.io/.../<svc>:1.0.0` with `@sha256:<digest>` pins generated by
  the build workflow (use `steps.build.outputs.digest`).
- Add a `terraform.tfvars.example` and document variable overrides per env.
- Add basic pytest unit tests per service (smoke-import is currently the gate);
  move them to `tests/` with `httpx` + FastAPI TestClient.
- Switch Vault from dev mode to raft storage + auto-unseal for production.
