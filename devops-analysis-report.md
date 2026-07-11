# DevOps Analysis — `stage/` Platform

Microservice platform (3 Python FastAPI services + HashiCorp Vault) deployed via Terraform libvirt IaC → Ansible CM → Kubernetes runtime, with GitHub Actions CI/CD. Comprehensive audit across 5 layers.

## Critical Findings (P0 — fix now)

| # | Issue | Location |
|---|---|---|
| 1 | **Vault root token `root-token-change-me` committed in 5+ places** (stringData, Cmd args, env, ConfigMap, Helm values) | `k8s/vault/secret-vault-root.yaml:15-16`, `manifests.yaml:68,78,135,184`, `values.yaml:22` |
| 2 | **DB creds live in ConfigMap** (not Secret), with plaintext passwords | `manifests.yaml:146,151,156` |
| 3 | **App fallback secrets hardcoded** — Vault failure → app signs JWT with `"fallback-dev-key"`. Fail-open anti-pattern | `users-service/main.py:13`, products `:13`, orders `:13`, `shared/vault_client.py:70-72` |
| 4 | **Terraform state has SSH pubkey in cleartext**, local backend with no locking → state corruption risk | `terraform/terraform.tfstate:65,80,95` |
| 5 | **CI test job swallows all failures** (`|| true`) — pipeline passes even if app broken | `.github/workflows/ci-cd.yml:92` |
| 6 | **`validate-security.sh` SKIP-on-missing-tool + `echo ok` check** — passes with zero tools installed | `scripts/validate-security.sh:52,62,116` |

## High Severity (P1)

| Issue | Location |
|---|---|
| No SAST, no DAST, no dependency scanning — semgrep ran ad-hoc only, never in CI | `ci-cd.yml` (absent jobs) |
| No actual deploy stage in CI (stops at trivy-scan) | `ci-cd.yml` |
| No `concurrency:` block — duplicate matrix runs race on registry push | `ci-cd.yml` |
| No `permissions:` least-privilege on 4/6 jobs | `ci-cd.yml:17,50,70,218` |
| Apps use `latest` tag + `imagePullPolicy: Never` — dev-only, non-reproducible | `k8s/apps/*-deployment.yaml:21-22` |
| No NetworkPolicies anywhere — namespace fully open internally | `k8s/` (absent) |
| No PodDisruptionBudgets despite HPA scaling 2→5 | `k8s/apps/hpa.yaml` |
| No anti-affinity / topologySpreadConstraints — replicas may co-locate on one node | `k8s/apps/*-deployment.yaml` |
| cloud-init grants NOPASSWD sudo to devops + no `ssh_pwauth: false` | `terraform/cloud-init.tpl:9` |
| Vault Deployment doesn't mount `vault-sa` — wasted SA | `k8s/vault/manifests.yaml:57-60` |
| Apps share one ServiceAccount — over-scoped (pods/services/watch): apps don't call K8s API | `k8s/apps/rbac.yaml:13-18` |
| `vault_client.py` duplicated 4× — single source of truth lost; drift guaranteed | `app/*/vault_client.py` (byte-identical) |

## Medium (P2)

- No `.dockerignore` → context ships `.git`/`__pycache__` (`app/*/Dockerfile`)
- Base images not digest-pinned (`python:3.11-slim`, `hashicorp/vault:1.15.2`) floats
- No `readOnlyRootFilesystem`, no `seccompProfile: RuntimeDefault`, no explicit `runAsUser` in any container
- Health endpoint returns hardcoded `{"status": "healthy"}` — no Vault/DB dep check; liveness=readiness (single probe)
- No structured logging (bare `print` in `vault_client.py:71`)
- Terraform lock constraint mismatch: `.terraform.lock.hcl:6` says `~> 0.7`, `main.tf:7` says `~> 0.9`
- No `validation` blocks on TF variables (`network_cidr`, `worker_count` unbounded)
- DNS hardcoded `1.1.1.1`,`8.8.8.8` ignores `var.dns_servers` (`terraform/main.tf:77-79`)
- Ansible: no `defaults/main.yml` in any role → roles not reusable standalone
- Ansible: missing `requirements.yml` for `community.general` (used at `k8s_common/tasks/main.yml:23`)
- Ansible: `k8s_reset` playbook guarded only by tag, not `when:` var — wipe risk
- Ansible: join token mode `0644` plaintext at `k8s_master/tasks/main.yml:159`
- ci-cd.yml:224 indentation appears broken (12-space vs 8) — verify with `actionlint`
- No Dependabot/Renovate config
- Trivy scans `:latest` race vs build pushing `:latest` — cross-job image drift
- No Vault Kubernetes auth method wired (SA exists, no `vault auth enable kubernetes`)
- `eks.amazonaws.com/role-arn: ""` empty annotation on `vault-sa` (non-EKS)

## What's Done Well

- All GitHub Actions pinned to commit SHA with version comments ✅
- Multi-stage Dockerfiles with non-root user `appuser` ✅
- requirements.txt exact `==` pinning across all services ✅
- Gitleaks + Trivy CRITICAL/HIGH gated + SBOM (spdx-json) artifact upload ✅
- All Ansible modules use FQCN (`ansible.builtin.*`, `community.general.*`) ✅
- Comprehensive per-task tagging (docker/k8s/master/worker/reset) ✅
- `command`/`shell` calls all guarded with `creates:` or `changed_when: false` ✅
- Worker join uses `serial: 1` (rolling, no thundering herd) ✅
- All Services `ClusterIP` (no public exposure) ✅
- Probes present on all Deployments + Vault ✅
- HPA per service `autoscaling/v2` ✅
- Terraform uses `for_each` + `templatefile` — proper DRY for libvirt ✅
- Vault dev mode + setup Job reseed on restart (safety net) ✅

## Top Priority Remediation Plan

1. **Rotate Vault root token.** Remove from `secret-vault-root.yaml`, `manifests.yaml`, `values.yaml`. Move DB creds from ConfigMap to Vault paths. Wire Kubernetes auth method per service with dedicated SAs.
2. **Fail-fast secrets in app.** Drop default fallback args from `get_secret("JWT_SECRET_KEY", "fallback-dev-key")` → raise instead. Split `/livez` vs `/readyz` (readyz checks Vault).
3. **Fix CI `test` job.** Drop `|| true`, add `pytest`, add `permissions: contents: read` top-level, add `concurrency:` block.
4. **Add SAST job.** Wire semgrep into `ci-cd.yml` (repo already has `semgrep-findings-report.md` — automate it).
5. **Remote Terraform state.** S3+DynamoDB or HTTP backend with locking. `.gitignore` `terraform.tfstate*`.
6. **Dedupe `vault_client.py`.** Make `app/shared/` a pip-installable package, import via `from shared.vault_client import get_secret`.
7. **Add NetworkPolicies + PDBs + topology spread** to `k8s/apps/`.
8. **Pin image digests.** Drop `latest` on app images; pin `python:3.11-slim@sha256:...` and `hashicorp/vault@sha256:...`.
9. **Harden cloud-init**: add `ssh_pwauth: false`, `disable_root: true`, restrict sudo via sudoers.d.
10. **Fix `validate-security.sh`**: `set -euo nounset`, fail (not skip) on missing tools in `--ci`, drop `echo "ok"` check, scan `:latest` not `:dev`, use `jq` not `python3`.

**Overall maturity: ~60%.** Strong foundation (IaC + CM + k8s manifests + CI scaffolding all present + scanned), but multiple critical secret-handling + CI gate weaknesses block production-readiness.

---

## Detailed Layer-by-Layer Findings

### 1. Terraform IaC (`terraform/`)

#### State Management
- **Backend:** local (`backend.tf:2-3`) with `path = "terraform.tfstate"` — no remote state, no centralized locking. Concurrent `terraform apply` on same host risks state corruption.
- **Secrets exposure CRITICAL:** `terraform.tfstate:65,80,95` contains full SSH public keys in cleartext in `user_data` field. State `terraform.tfstate:67,82,97` shows `sensitive_attributes: []` — provider does not mark cloud-init user_data as sensitive.
- **Backup file** `terraform.tfstate.backup` exists with same exposure risk.

#### Provider Pinning
- `main.tf:2` — `required_version = ">= 1.5.0"` — lower-bound only, no upper bound. State shows `terraform_version: "1.15.7"`.
- `main.tf:6` — libvirt `~> 0.9`, `main.tf:11` — local `~> 2.5` — acceptable.
- **MISMATCH:** `.terraform.lock.hcl:6` has `constraints = "~> 0.7"` vs `main.tf:7` `~> 0.9` — fresh clone would confuse `terraform init`.
- Provider hashes present (lock.hcl:8-22, :28-43) — integrity verified. Good.

#### Variable Definitions
- All 18 variables have type constraints (string/number/list(string)).
- **NO variable marked `sensitive = true`.** `ssh_public_key` (`variables.tf:103-107`) should be.
- **No `validation` blocks anywhere.** `network_cidr` accepts arbitrary string; `worker_count` no min/max bound (could be negative).

#### Resource Naming / Tagging
- Consistent names: `libvirt_network.platform`, `libvirt_volume.base/node`, `format("worker-%02d", index+1)` zero-padded.
- **Zero tags/metadata labels.**

#### Security
- Private NAT network: `forward.mode = "nat"` (`main.tf:55`), `local_only = "yes"` (`main.tf:52`), CIDR `192.168.56.0/24`.
- VNC bound to `127.0.0.1` (`main.tf:242`) — not externally exposed.
- **SSH key auto-detect** (`main.tf:22-26`) silently picks up dev's personal key from `~/.ssh/id_ed25519.pub` then `id_rsa.pub`.
- **No `ssh_pwauth: false`, no `disable_root: true`, no firewall**, no fail2ban. Password auth defaults depend on cloud image.
- `cloud-init.tpl:9` — `sudo: ALL=(ALL) NOPASSWD:ALL` unrestricted passwordless sudo.

#### Modularization
- Not modularized. Single `main.tf` (261 lines), no `modules/` dir, no workspaces. Acceptable for small libvirt homelab.

#### DRY / Reuse
- Good: `for` over `range(var.worker_count)` (`main.tf:34-40`), `for_each = local.nodes` (volumes, cloud-init, domains), `cidrhost(var.network_cidr, ...)` computes IPs.
- **DRY violation:** DNS hardcoded `{addr="1.1.1.1"}, {addr="8.8.8.8"}` (`main.tf:77-79`) ignores `var.dns_servers`.
- `gateway_ip` default `192.168.56.1` should derive via `cidrhost(var.network_cidr, 1)`. `network_prefix` default `24` duplicates prefix from `network_cidr`.

#### Outputs
- 5 outputs, **none marked `sensitive = true`.** `ansible_inventory_content` (`outputs.tf:23`) exposes IPs + ssh_user in plan output.

#### Drift / Reproducibility
- Base image path hardcoded: `/var/lib/libvirt/images/ubuntu-22.04-server-cloudimg-amd64.img` (`variables.tf:22`) — host-specific.
- `main.tf:114-116` hardcoded UID `64055` / GID `993` (specific libvirt install) — non-portable.
- No `lifecycle` blocks except `ssh_key_guard` precondition (`main.tf:164-169`).
- `package_upgrade: false` (`cloud-init.tpl:22`) — VMs don't auto-upgrade. Drift between manually-patched nodes.

### 2. Ansible Configuration Management (`ansible/`)

#### Inventory
- Static INI, hardcoded IPs: `master-01 ansible_host=192.168.56.10`, worker-01 `:11`, worker-02 `:12`. No dynamic inventory plugin, no `host_vars/`.
- All hosts `ansible_user=devops`. No plaintext passwords — SSH key auth via cfg.

#### group_vars / vault
- Only `group_vars/all.yml` (8 lines): k8s_version, calico_version, pod_cidr, service_cidr, containerd_socket, cri_endpoint.
- **No `ansible-vault` anywhere.** No `vault_password_file` in cfg. Future secrets would ship plaintext.
- No `group_vars/masters.yml`, no `host_vars/`.

#### ansible.cfg (13 lines)
- `inventory = inventory.ini` ✅
- `host_key_checking = False` (L3) — risky on prod, ok on disposable VMs
- `remote_user = devops`, `private_key_file = ~/.ssh/id_ed25519`
- `roles_path = roles` (relative), `timeout = 30`
- `retry_files_enabled = False` ✅, `pipelining = True` (L9) **duplicated** at L13
- **Missing:** `forks`, `vault_password_file`, `stdout_callback`, `collections_path`
- Callback FQCN `ansible.builtin.timer` ✅

#### Playbook (41 lines)
- 5 plays, all role-based with `become: true`.
- Worker join uses `serial: 1` (L29) ✅ rolling, no thundering herd.
- **`k8s_reset` play in same file as bootstrap** (L35-41), guarded only by tag — wipe risk on misinvoked `ansible-playbook`.
- No `pre_tasks`/`post_tasks`, no `any_errors_fatal`.

#### Role structure
- 5 roles: `docker`, `k8s_common`, `k8s_master`, `k8s_worker`, `k8s_reset`.
- **No `defaults/main.yml` in any role** (only `group_vars/all.yml`) → roles not reusable standalone.
- **No `meta/main.yml`** — no Galaxy metadata, no `requires_ansible` version, not publishable.
- **No `templates/`** anywhere — inline `copy:` with `content:` used throughout.
- Empty `handlers/main.yml` dirs in 3 roles — scaffolding leftovers.

#### Idempotency
- Strong: 17 `command`/`shell` calls, all guarded with `creates:`, `changed_when: false`, or `register` + retry.
- `kubectl apply` with `changed_when: false` (`k8s_master/tasks/main.yml:84,109`) masks real config drift reporting.
- `k8s_worker/tasks/main.yml:29-35` restarts kubelet inline after join — not idempotent (restart still fires when join did nothing via `creates:`).

#### Privilege escalation
- `become: true` on all 5 plays (no per-task override, no `become_user`). Defaults to sudo with `remote_user = devops` implying devops has passwordless sudo — **undocumented assumption**.

#### Handlers
- Only 1 handler across whole codebase: `docker/handlers/main.yml:2-7` `Restart containerd`.
- Empty handlers dirs in 3 roles. `k8s_reset` has no handlers dir — inconsistent.

#### Secret management
- No vault. `k8s_master/tasks/main.yml:159` writes join command to `/tmp/kubeadm-join.txt` mode **0644** — plaintext readable by any local user. Should be `0600` owner `devops`.

#### Tagging
- Excellent: per-play + per-task tags (`docker`, `k8s`, `master`, `worker`, `reset`) consistent across all 59 named tasks. Allows `--tags`, `--skip-tags`.

#### FQCN
- All modules FQCN ✅ (`ansible.builtin.*`, `community.general.modprobe` at `k8s_common/tasks/main.yml:23`).
- **Missing `requirements.yml`** declaring `community.general` — fresh workstation would break.
- Role names unnamespace (`docker`, `k8s_*`) — fine locally, not Galaxy-compliant.

### 3. Kubernetes Manifests (`k8s/`)

**Inventory:** 9 files, ~625 lines. Apps in namespace `devops-platform`, vault in `vault`.

#### Namespace Usage
- Apps reference `devops-platform` but **no Namespace manifest declares it** (`apps/*-deployment.yaml:5`). Only `vault` Namespace explicitly created (`manifests.yaml:3-9`). Asymmetric.

#### Deployments / Probes / Resources
- All apps replicas=2, default `RollingUpdate`. Vault replica=1 `Recreate` (correct for dev mode).
- **Liveness+readiness present on all** apps + vault ✅. No `failureThreshold`/`timeoutSeconds`/`startupProbe` configured (defaults used).
- All have resources requests+limits (apps 100m/250m CPU, 128Mi/256Mi mem; vault 100m/500m, 256Mi/512Mi).
- **vault-setup Job has NO resources** (`manifests.yaml:174-184`) — unbounded if no ResourceQuota.

#### SecurityContext (consistent gaps across all containers)
- `runAsNonRoot: true` ✅, `allowPrivilegeEscalation: false` ✅
- **MISSING everywhere:** `readOnlyRootFilesystem: true`, explicit `runAsUser`, `runAsGroup`, `fsGroup`, `seccompProfile: RuntimeDefault`.
- Vault adds `IPC_LOCK` capability (mlock) — acceptable. No `drop: ["ALL"]` anywhere.

#### Services
- All 4 ClusterIP, no public exposure ✅. App ports unnamed; vault ports named (asymmetric).

#### ConfigMaps / Secrets — CRITICAL
- **Vault root token `root-token-change-me` committed in 5+ places** (see P0 table).
- **DB creds in ConfigMap** `manifests.yaml:146,151,156` (`users_user:users_pass`, `jwt-secret-for-users-service`, `products-api-key-12345`, `payment-gw-key-67890`).
- Apps fetch root token via `secretKeyRef` with **`optional: true`** — silently continues without token if missing. Dangerous.
- No Sealed Secrets / SOPS / external encryption.

#### Ingress
- Zero Ingress objects anywhere. Internal-only platform. Add if external traffic path needed.

#### RBAC / ServiceAccounts
- `devops-platform-sa` (`rbac.yaml:1-5`) shared by all 3 apps — over-scoped (pods/services/configmaps get/list/watch + deployments get/list/watch). Apps don't call K8s API at runtime. Set `automountServiceAccountToken: false`.
- `vault-sa` (`manifests.yaml:196-202`) exists but **Vault Deployment does NOT reference it** (`manifests.yaml:57-60`) — wasted SA. Same for setup Job.
- `eks.amazonaws.com/role-arn: ""` empty annotation on `vault-sa` — remove (non-EKS).

#### NetworkPolicies
- Zero. Namespace fully open internally. Recommend default-deny + allow-list: apps→vault:8200, apps→postgres, vault-setup→vault:8200.

#### PodDisruptionBudgets
- Zero. HPA scales 2→5 but no PDB protects min availability during node drain.
- Vault replica=1 + Recreate → no HA. Document dev-only.

#### HPA
- 3 HPAs `autoscaling/v2`, CPU avgUtilization 70%, min 2 / max 5 ✅.
- CPU-only (no memory/custom metric). No `behavior` block (scaleDown thrash risk).
- No PDB alongside (see above).

#### Image References
| Container | tag | digest | pullPolicy |
|---|---|---|---|
| apps (3×) | **latest** | none | Never (local-only) |
| vault (deploy + job) | 1.15.2 | none | IfNotPresent |

- `latest` + `Never` = dev-only footprint. Pin semver + digest for prod.

#### Vault Integration
- **Dev mode** in-memory, auto-unsealed, hardcoded root token. README acknowledges (`README.md:78-83`).
- Apps use root token via env `VAULT_TOKEN` secretKeyRef — every microservice has full Vault root power. Should use per-service policies + Kubernetes auth method with each app's own SA → short-lived tokens.
- No `vault auth enable kubernetes` performed in setup.sh — just `vault kv put`. K8s auth only documented, not wired.
- HTTP only (`http://vault-service.vault.svc.cluster.local:8200`) — no TLS. Dev ok, prod must add.
- Setup Job robust (polls readiness loop). Re-seeds on restart — safety net for in-memory backend.

#### Helm vs Raw Manifests
- Both present side-by-side for Vault (Helm `values.yaml` + raw `manifests.yaml`) — duplication. README offers Option A/B. Pick one for prod.
- No `Chart.yaml`, no Kustomize `kustomization.yaml` — no overlay structure for dev/stage/prod.
- No CI lint declared (no kubeval/kubeconform/ct).

#### Labels / Annotations
- Two vocabularies coexist: apps use bare `app: <svc>`, vault uses `app.kubernetes.io/name` + `app.kubernetes.io/part-of`.
- Apps missing standard labels entirely (no `version`, `managed-by`, `part-of`).
- HPA metadata no labels. Role/RoleBinding no labels.
- No `prometheus.io/scrape` annotations for monitoring — zero metrics scrape config (despite apps exposing `/metrics`).

#### Anti-Affinity / Topology Spread
- Zero `podAntiAffinity`, zero `topologySpreadConstraints`. Scheduler may co-locate all replicas on one node. Node failure = entire app down despite replicas=2.

#### Additional Gaps
- No `ResourceQuota` / `LimitRange` in either namespace.
- No `PriorityClass`.
- No Pod Security Admission labels (`pod-security.kubernetes.io/enforce: restricted`) — `runAsNonRoot: true` voluntary, not enforced.
- No init containers to wait for Vault readiness — apps likely fail-loop if Vault not ready at startup.
- No GitOps evidence (no ArgoCD sync-wave annotations).

### 4. CI/CD (`.github/workflows/ci-cd.yml`)

#### Pipeline stages
6 jobs: `lint` → `gitleaks` → `test` → `build` (matrix) → `trivy-scan` (matrix). Separate `terraform-validate` independent (no `needs:`).
- **No `deploy` stage** — pipeline stops at trivy-scan. No kubectl/helm/argocd.
- `terraform-validate` not gated by lint/test.

#### Secret handling
- `GITHUB_TOKEN` used for docker login + trivy + gitleaks ✅ auto-rotated.
- No hardcoded creds in workflow ✅.
- App has hardcoded fallback secrets (see P0 #3).
- `.gitleaks.toml:21` allowlists `.*\.md$` — could hide secrets in markdown docs. `:27` allowlists `changeme` regex — masks `change-me`.

#### Permissions (least privilege)
- No top-level `permissions:` block. 4/6 jobs inherit broad default.
- `build` (L109-111) `contents: read, packages: write` ✅.
- `trivy-scan` (L168-170) `contents: read, packages: read` ✅.
- `lint`, `gitleaks`, `test`, `terraform-validate` — none declared, too broad.
- `gitleaks` job wants PR comments (`GITLEAKS_ENABLE_COMMENTS=true` L62) but lacks `pull-requests: write`.

#### Action pinning
- **Excellent** — all actions pinned to commit SHA with version comment (checkout, setup-python, gitleaks, docker setup-buildx/login/metadata/build-push, trivy, upload-artifact, setup-terraform).
- Trivy pinned to `# master @ 0.24.0` comment — SHA frozen so safe, just fragile mental note.

#### Concurrency
- **None.** No `concurrency:` block. Concurrent pushes/PRs race on registry push for `:latest`.

#### Environment protections / deployment gates
- **None.** No `environment:` key on any job. No protected promotion path main→prod.

#### Caching
- Docker layer cache via GHA (`cache-from: type=gha`, `cache-to: type=gha,mode=max`, L145-146) ✅.
- setup-python **missing `cache: pip`** (L23, L77) — reinstall every job.
- No `actions/cache` for terraform `.terraform/` — re-downloads each run.

#### Triggers
- `push: branches: [main, develop]`, `pull_request: branches: [main]`.
- No `schedule` (no nightly drift), no `workflow_dispatch`, no `paths:` filters (docs-only changes rebuild images).
- PR only targets main — asymmetric vs push.

#### Matrix
- `build` + `trivy-scan` matrix `service: [users-service, products-service, orders-service]` ✅ parallel.
- Default `fail-fast: true` — one service cancels siblings. Recommend `fail-fast: false` for independent services.
- `build` outputs (L157-158) declares only `users-image` — `products-image`/`orders-image` missing.

#### Security scanning
| Control | Status | Ref |
|---|---|---|
| Secret scan (gitleaks) | ✅ blocking-ish (test `needs` it) | L50-65, 73 |
| Container vuln scan (trivy) | ✅ CRITICAL,HIGH exit 1 | L190-198 |
| SBOM (spdx-json) | ✅ uploaded artifact | L200-213 |
| Dependency scan (pip-audit/safety) | ❌ MISSING | — |
| SAST (semgrep/CodeQL) | ❌ MISSING (semgrep ran once ad-hoc, generated report file only) | root:semgrep-findings-report.md |
| DAST (ZAP/nuclei) | ❌ MISSING | — |
| Dependency review (PR) | ❌ MISSING | — |
| Dependabot | ❌ MISSING | — |

#### Artifact handling
- SBOM uploaded `if: always()` ✅. No retention config (default 90d).
- Trivy pulls `:latest` (L186) — race vs build pushing `:latest`. Cross-job image drift.

#### Deployment steps
- **NONE.** Pipeline ends at trivy-scan. `terraform-validate` only validates.

#### Syntax
- ci-cd.yml:224 indent appears broken (12-space vs 8-space expected). Verify with `actionlint`.

### 5. Application Code (`app/`)

#### Dockerfiles (3 near-identical multi-stage)
- ✅ Multi-stage (builder → final).
- ✅ Non-root user `appuser` (Dockerfile:11, USER :13).
- ✅ `--no-cache-dir`, `python:3.11-slim`.
- ❌ No `.dockerignore` anywhere — context ships `.git`, `__pycache__`.
- ❌ Base image not digest-pinned (`python:3.11-slim` floats).
- ❌ `--user` pip install into `/root/.local` then run as appuser — works via PATH but smell.
- ❌ No `HEALTHCHECK`, no `LABEL`, no `COPY --chown=appuser:appuser`, no `--platform`.

#### Dependency pinning
- ✅ All 4 packages exact `==` (fastapi 0.104.1, uvicorn 0.24.0, prometheus-client 0.17.1, hvac 2.1.0).
- ❌ No hashes (`--require-hashes`), no transitive pinning (no `pip-compile`/`uv lock` output), no constraints file.
- ❌ No Dependabot/Renovate config.
- ✅ Versions consistent across services.

#### App config (env/vault)
- env: `SERVICE_NAME`, `VAULT_ADDR`. Vault: `DATABASE_URL`, `JWT_SECRET_KEY` (users), `API_KEY` (products), `PAYMENT_GATEWAY_KEY` (orders).
- `vault_client.py:30-31` `VAULT_ADDR` default `http://vault-service.vault.svc.cluster.local:8200`.
- `vault_client.py:34-36` reads `VAULT_TOKEN` or `VAULT_DEV_ROOT_TOKEN_ID`.
- **Silent fallback** (`vault_client.py:70-72`): on Vault failure, swallows exception, returns `{}`, then `get_secret` falls to env, then to default. **Prod runs with fake secrets.** Fail-open antipattern.
- `lru_cache(maxsize=1)` (`vault_client.py:51`) — process-lifetime cache, no rotation. `reload_secrets()` exists but no caller in main.py.
- No config validation — missing required var → silent fallback.

#### Health endpoints
- All 3: `/health` returns `{"status": "healthy"}` hardcoded (no Vault/DB dep check). `/metrics` Prometheus ✅. Per-service Counter names ✅.
- ❌ Liveness=readiness (single probe). No `/livez`/`/readyz` split. No dependency check.

#### Logging
- ❌ No structured logging. `vault_client.py:71` bare `print(f"[vault_client] Warning: ...")`. No `logging` import in main.py. FastAPI default access log to stderr.

#### Shared library — DUPLICATED
- `app/shared/vault_client.py` exists (91 lines) but **each service has byte-identical copy**: `users-service/vault_client.py`, `products-service/`, `orders-service/`. Single source of truth lost; drift guaranteed.
- `app/shared/` has no `__init__.py`, no `pyproject.toml` — not importable.
- Dockerfile:9 `COPY main.py vault_client.py .` copies local, ignores `app/shared/`.
- CI lints the 3 services, never `app/shared/`.

### 6. Security Script (`scripts/validate-security.sh`, 133 lines)

4 sequential checks: Gitleaks, Trivy, Vault unsealed, K8s secret existence. Optional `--ci` mode.

**Not robust:**
1. **Silent skip-on-missing-tool** (L52,62,80,108,114,120): gitleaks/trivy/kubectl absent → SKIP, TOTAL decremented, no FAIL. Machine with no tools → all pass.
2. **`--no-git` gitleaks** (L53) misses history — diverges from CI (`fetch-depth: 0`).
3. **`:dev` tag mismatch** (L66) vs CI `:latest` (ci-cd.yml:153) → script never scans CI-built images.
4. **Check 4 is no-op** (L116) — `echo "ok"` always succeeds. Doesn't verify token works or rotates.
5. **No `set -euo nounset`** (only `set -o pipefail` L17) — unset vars don't error.
6. Uses `python3` for JSON parsing (L88-89) — fragile if python missing. Better: `jq`.
7. Not invoked in CI — local-only. Effective CI security = gitleaks-action + trivy-action only.
- Final `exit $FAIL` (L133) returns fail count (0..4) — semantically odd, should be `exit 1` on any fail.

---

*Generated by DevOps best-practices audit. All file:line references point into `/home/gadour/Desktop/stage/`.*
