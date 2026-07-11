Phase 4 — DevSecOps Implementation Report
Objective
Integrate security as a blocking step in the CI/CD pipeline (not an afterthought). Three pillars: secret scanning, container vulnerability scanning, and dynamic secret management via HashiCorp Vault.
1. Secret Scanning — Gitleaks
New file: .gitleaks.toml
Gitleaks configuration file at the project root. Extends default Gitleaks rules and adds a project-specific allowlist to avoid false positives:
- Paths excluded: terraform.tfstate, files.md/, .gitleaks.toml itself, terraform/terraform.tfvars, all *.md files
- Regex patterns excluded: common placeholder strings (example, changeme, YOUR_SECRET_HERE, REPLACE_ME)
CI Integration — .github/workflows/ci-cd.yml
Gitleaks runs as a dedicated gitleaks job, in parallel with lint, but before the test job (which depends on both lint and gitleaks). Any secret detection fails the job immediately, preventing further pipeline stages from running.
Pipeline dependency chain:
lint ──────────┐
               ├──→ test ──→ build ──→ trivy-scan (BLOCKING)
gitleaks ──────┘
terraform-validate (independent, parallel)
2. Container Scanning — Trivy
CI Integration — .github/workflows/ci-cd.yml (same file)
Trivy runs as a trivy-scan job that depends on build. It pulls each built image from the registry and scans it with these parameters:
- severity: CRITICAL,HIGH — only these levels trigger failure
- exit-code: 1 — fails the pipeline if vulnerabilities found
- ignore-unfixed: true — ignores vulnerabilities with no available fix
- vuln-type: os,library — scans OS packages and application libraries
A non-blocking SBOM generation step also runs (SPDX JSON format) and uploads the artifact for traceability.
The job uses a matrix strategy across all 3 services (users-service, products-service, orders-service).
3. Complete CI/CD Pipeline — .github/workflows/ci-cd.yml
New file. 6 jobs total:
Job	Trigger	Blocking?	What it does
lint	on push/PR	No	Ruff lint Python code + terraform fmt check + YAML lint
gitleaks	on push/PR	Yes	Secret scan with full git history (fetch-depth: 0)
test	needs lint+gitleaks	No	Python syntax validation + Docker build test
build	needs test	No	Multi-stage Docker build + push to GHCR, matrix per service
trivy-scan	needs build	Yes	Vulnerability scan + SBOM generation
terraform-validate	on push/PR	No	terraform init -backend=false + terraform validate
Build job also generates provenance attestation (provenance: true) and SBOM (sbom: true) via Docker Buildx for supply chain security.
Triggers: push to main or develop, pull_request to main.
4. HashiCorp Vault — Secret Management
New directory: k8s/vault/
4 files created:
a) k8s/vault/manifests.yaml — Raw Kubernetes manifests:
- Namespace: vault
- Service: vault-service (ClusterIP, ports 8200/8201)
- Deployment: single Vault pod in dev mode (-dev, -dev-root-token-id=root-token-change-me)
- ConfigMap: vault-setup containing a setup script that:
- Waits for Vault to be ready (up to 60 retries, 2s interval)
- Enables KV v2 secrets engine at path secret/
- Seeds secrets for all 3 microservices:
- secret/devops-platform/users-service: DATABASE_URL, JWT_SECRET_KEY
- secret/devops-platform/products-service: DATABASE_URL, API_KEY
- secret/devops-platform/orders-service: DATABASE_URL, PAYMENT_GATEWAY_KEY
- Job: vault-setup-job runs the script once (TTL 300s)
- ServiceAccount: vault-sa
- Role + RoleBinding: RBAC for secrets access
b) k8s/vault/values.yaml — Helm chart values for hashicorp/vault:
- Dev mode enabled with root token
- Resource limits: 500m CPU / 512Mi memory
- UI enabled on port 8200
- Injector disabled (services fetch secrets at startup, not via sidecar)
c) k8s/vault/secret-vault-root.yaml — Kubernetes Secret in devops-platform namespace:
stringData:
  root-token: "root-token-change-me"
Referenced by microservice deployments via secretKeyRef.
d) k8s/vault/README.md — Deployment guide covering both Helm and raw manifest approaches, secret paths table, verification commands, and security notes about dev mode vs. production.
5. Microservices — Vault Integration
New file: app/shared/vault_client.py
Shared utility module used by all 3 microservices. Key design:
- Resolution order: Vault → environment variable → default parameter
- Caching: @lru_cache(maxsize=1) — secrets fetched once at startup, reused for process lifetime
- Graceful degradation: if VAULT_ADDR or VAULT_TOKEN not set, falls back to environment variables silently
- reload_secrets(): cache-clearing function for test/long-lived use
Function get_secret(name, default) resolves from:
1. Vault path secret/data/devops-platform/<SERVICE_NAME>
2. os.environ[name]
3. default argument
New file: app/shared/requirements.txt
Shared dependencies: fastapi, uvicorn, prometheus-client, hvac==2.1.0.
Changed file: app/users-service/main.py
- Added imports: os, from vault_client import get_secret
- New module-level variables:
- DATABASE_URL = get_secret("DATABASE_URL", "sqlite:///./users.db")
- JWT_SECRET_KEY = get_secret("JWT_SECRET_KEY", "fallback-dev-key")
- SERVICE_NAME, VAULT_ADDR, VAULT_CONFIGURED
- Root endpoint now returns vault_configured boolean (for health check / verification)
Changed file: app/products-service/main.py
Same pattern:
- DATABASE_URL, API_KEY fetched from Vault with fallbacks
- Root endpoint includes vault_configured
Changed file: app/orders-service/main.py
Same pattern:
- DATABASE_URL, PAYMENT_GATEWAY_KEY fetched from Vault with fallbacks
- Root endpoint includes vault_configured
Changed file: app/users-service/requirements.txt
Added: hvac==2.1.0
Changed file: app/products-service/requirements.txt
Added: hvac==2.1.0
Changed file: app/orders-service/requirements.txt
Added: hvac==2.1.0
Changed file: app/users-service/Dockerfile
Added COPY main.py vault_client.py . (was COPY main.py .)
Changed file: app/products-service/Dockerfile
Added COPY main.py vault_client.py . (was COPY main.py .)
Changed file: app/orders-service/Dockerfile
Added COPY main.py vault_client.py . (was COPY main.py .)
Copied files (for Docker build context):
- app/users-service/vault_client.py ← app/shared/vault_client.py
- app/products-service/vault_client.py ← app/shared/vault_client.py
- app/orders-service/vault_client.py ← app/shared/vault_client.py
(Necessary because Docker COPY cannot access paths outside the build context — each service directory now has its own copy.)
6. Kubernetes Deployments — Vault Configuration
Changed file: k8s/apps/users-deployment.yaml
- Added serviceAccountName: devops-platform-sa to pod spec
- Added 3 environment variables to container:
- SERVICE_NAME=users-service
- VAULT_ADDR=http://vault-service.vault.svc.cluster.local:8200
- VAULT_TOKEN via secretKeyRef from vault-root-token secret key root-token (optional)
Changed file: k8s/apps/products-deployment.yaml
Same changes:
- serviceAccountName: devops-platform-sa
- SERVICE_NAME=products-service, VAULT_ADDR, VAULT_TOKEN via secretKeyRef
Changed file: k8s/apps/orders-deployment.yaml
Same changes:
- serviceAccountName: devops-platform-sa
- SERVICE_NAME=orders-service, VAULT_ADDR, VAULT_TOKEN via secretKeyRef
7. Validation Script
New file: scripts/validate-security.sh (executable)
Runs 4 checks (some skip if tools not installed):
1. Gitleaks — gitleaks detect --source . --config .gitleaks.toml --no-git → must find 0 leaks
2. Trivy — scans users-service:dev, products-service:dev, orders-service:dev with --severity CRITICAL,HIGH --exit-code 1 (skips if images don't exist locally)
3. Vault status — queries Vault pod with vault status -format=json, checks initialized=true and sealed=false
4. K8s Secret — verifies vault-root-token secret exists in devops-platform
- --ci flag: exits 1 on any failure
- Without flag: prints a colored summary table
- Handles missing tools gracefully (shows ⚠️ SKIP)
8. Documentation
New file: files.md/Phase_4_Etapes.md
Full Phase 4 documentation including:
- Objective summary
- Operational steps for each component (Gitleaks, Trivy, Vault deploy, seed secrets, verify)
- Verification criteria
- Status table showing all implemented items
Summary: Files Changed/Created
Category	File	Action
Secret scan	.gitleaks.toml	Created
CI/CD	.github/workflows/ci-cd.yml	Created
Vault K8s	k8s/vault/manifests.yaml	Created
Vault K8s	k8s/vault/values.yaml	Created
Vault K8s	k8s/vault/secret-vault-root.yaml	Created
Vault K8s	k8s/vault/README.md	Created
Shared lib	app/shared/vault_client.py	Created
Shared deps	app/shared/requirements.txt	Created
Microservices	app/users-service/main.py	Modified (+vault import, +secrets, +vault_configured)
 	app/users-service/Dockerfile	Modified (+vault_client.py COPY)
 	app/users-service/requirements.txt	Modified (+hvac)
 	app/products-service/main.py	Modified
 	app/products-service/Dockerfile	Modified
 	app/products-service/requirements.txt	Modified
 	app/orders-service/main.py	Modified
 	app/orders-service/Dockerfile	Modified
 	app/orders-service/requirements.txt	Modified
K8s deployments	k8s/apps/users-deployment.yaml	Modified (+sa, +env vars)
 	k8s/apps/products-deployment.yaml	Modified (+sa, +env vars)
 	k8s/apps/orders-deployment.yaml	Modified (+sa, +env vars)
Validation	scripts/validate-security.sh	Created (chmod +x)
Docs	files.md/Phase_4_Etapes.md	Created
Verification: Ruff clean. YAML lint clean. Python AST valid. Docker COPY paths correct. Pipeline job graph verified (lint+gitleaks → test → build → trivy-scan).