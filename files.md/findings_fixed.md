Summary — Remediation Complete
All P0 (6), P1 (12), and P2 (~20) findings from devops-analysis-report.md now fixed.
P0 (critical)
1. Vault root token purged from all 5 git-tracked locations. New scripts/bootstrap-vault-secret.sh injects an out-of-band token via stdin → kubectl so it never lands in ps/history/disk. Placeholder Secret file is empty-on-purpose with a scan marker.
2. DB creds removed from ConfigMap — now seeded into Vault KV paths only by the setup Job.
3. App fail-open anti-pattern eliminated — shared/vault_client.py now raises SecretUnavailable; each main.py calls _load_secrets() at startup and SystemExits when ENVIRONMENT != dev and a secret is missing. Verified by execution: services properly refuse to start without Vault.
4. Terraform state untracked — terraform.tfstate* + generated inventories git rm --cached; new .gitignore blocks all *.tfstate*; backend.tf documents S3 migration; ssh_public_key variable marked sensitive = true; all 5 outputs marked sensitive.
5. CI test job fixed — dropped \|\| true; added pip-audit dependency scan, pytest smoke test, real pip install -e app/shared/ import paths.
6. validate-security.sh rewritten — set -euo pipefail, fail (not skip) on missing tools in --ci, dropped the echo ok no-op (now actually probes Vault token validity via vault token lookup), uses jq, scans :latest.
P1 (high)
- CI: Semgrep SAST job wired in (SARIF uploaded); per-job permissions: least-privilege + top-level contents: read; concurrency: cancel-in-progress; paths: filters; fail-fast: false; nightly drift schedule; workflow_dispatch deploy stage gated by protected production environment; kubeconform in lint job; indent bug at L224 fixed.
- k8s: per-service SAs (users-service-sa, etc., replacing the shared over-scoped SA); dedicated vault-sa now mounted on both the Vault Deployment and the setup Job; NetworkPolicies (default-deny + allow-list: app→vault, app→dns, app→postgres, prometheus-scrape, intra-namespace); PodDisruptionBudgets per service; topologySpreadConstraints + podAntiAffinity; pinned image semver (ghcr.io/.../<svc>:1.0.0); optional: false on VAULT_TOKEN so pods fail-closed; structured logging env wired.
- Pod Security Admission restricted labels on both namespaces enforce non-root, seccomp, no priv-escalation.
- Namespace + LimitRange + ResourceQuota now declared.
- cloud-init hardened: ssh_pwauth: false, disable_root: true, package_upgrade: true, restricted sudo: allowlist (kubeadm/kubelet/containerd only), fail2ban installed, runcmd rewrites sshd_config.
- vault_client.py deduplicated 4× → single source of truth in app/shared/ made a real pip package (pyproject.toml, __init__.py); per-service copies deleted; Dockerfiles build via context: app/.
P2 (medium)
- .dockerignore excludes .git, __pycache__, tfstate, *.html, *.md, k8s/terraform/ansible/scripts.
- Dockerfiles hardened: digest-pinnable via ARG BASE_IMAGE, COPY --chown, HEALTHCHECK via urllib, LABEL OCI metadata, PYTHONUNBUFFERED=1, LOG_FORMAT=json, ENVIRONMENT-aware.
- All containers: readOnlyRootFilesystem: true + seccompProfile: RuntimeDefault + explicit runAsUser/runAsGroup/fsGroup + drop: ["ALL"].
- Health split: /livez (liveness, no deps) vs /readyz (readiness checks Vault via vault_health()). Compatibility /health alias kept.
- Structured JSON logging via shared/logging.py + python-json-logger.
- TF: validation blocks on network_cidr/worker_count/vm_vcpu/vm_memory_mb/disk_size_gb/network_prefix; gateway_ip derived from cidrhost(var.network_cidr, 1); dns forwarders wired to var.dns_servers; netmask via cidrnetmask; lock constraint ~> 0.7 → ~> 0.9; UID/GID 64055/993 → var.libvirt_volume_owner_uid/gid; required_version = "~> 1.5" with upper bound.
- Ansible: per-role defaults/main.yml + meta/main.yml (Galaxy-compliant); new requirements.yml declares community.general; ansible.cfg adds forks=10, dedupes pipelining, sets stdout_callback=yaml; k8s_reset play now gated by when: reset_confirmed | bool + never tag (can no longer be triggered by accident); kubeadm join token mode 0644 → 0600 with owner devops.
- HPA: memory metric added + behavior.scaleUp/stabilizationWindowSeconds + scaleDown policy to prevent thrash.
- .gitleaks.toml allowlist tightened: .*\.md$ blanket removed, changeme regex removed (was masking the actual root-token-change-me).
- Dependabot config for pip (per-service + shared), docker, github-actions.
- Vault K8s auth method auto-configured by the setup Job (vault auth enable kubernetes, per-service policies + role bindings); injector enabled in Helm values.
Verification (all passing)
- ruff check on app/shared/ + 3 services: clean
- yamllint k8s/ ansible/ .github/: clean
- terraform fmt -check -recursive: clean
- terraform validate: "Success! The configuration is valid."
- Python smoke: shared package imports + each service main.py compiles + fail-fast SystemExit fires correctly when Vault unreachable in production mode.
- Repo-wide grep: zero remaining root-token-change-me, fallback-dev-key, or optional: true on VAULT_TOKEN.
New files
- scripts/bootstrap-vault-secret.sh — out-of-band root-token injector
- k8s/apps/namespace.yaml — Namespace + LimitRange + ResourceQuota + PSA labels
- k8s/apps/networkpolicies.yaml — default-deny + allow-list (6 policies)
- k8s/apps/pdbs.yaml — 3 PDBs
- app/shared/{__init__.py, config.py, logging.py, pyproject.toml} — real Python package
- app/shared/requirements.txt — pinned shared deps
- ansible/requirements.yml + per-role defaults/main.yml + meta/main.yml
- .github/dependabot.yml, .gitignore, .dockerignore, AGENTS.md, devops-analysis-report.md
Production follow-up (documented in AGENTS.md)
- Wire Vault Agent Injector annotations + drop VAULT_TOKEN env (K8s auth is already configured).
- Replace :1.0.0 tags with @sha256:<digest> pins from build digest output.
- Add terraform.tfvars.example; pytest unit tests per service; raft storage for Vault.
Nothing committed — all changes staged in working tree, ready for you to review via git diff --cached and commit when satisfied.