# Phase 4 — Security pipeline (DevSecOps)

Phase 4 integrates security as blocking CI steps — secrets never merged, vulnerable images never deployed. Secrets come from HashiCorp Vault at runtime, not from environment variables.

## Objective

- Integrate GitLeaks for secret scanning as the first CI step (fails the pipeline if any secret is detected).
- Integrate Trivy for container vulnerability scanning after the Docker build (fails the pipeline on CRITICAL/HIGH vulnerabilities).
- Deploy HashiCorp Vault in the cluster to manage secrets dynamically.
- Update microservices to fetch credentials from Vault at startup instead of using static environment variables.

## Operations steps

### 1. Secret scanning (Gitleaks)

```bash
# Local test — must return 0 scans with findings
gitleaks detect --source . -v

# Test in CI mode against all commits
gitleaks detect --source . --no-git
```

### 2. Container vulnerability scanning (Trivy)

```bash
# Build images locally
docker build -t users-service:dev app/users-service/
docker build -t products-service:dev app/products-service/
docker build -t orders-service:dev app/orders-service/

# Scan — must return 0 CRITICAL/HIGH vulnerabilities
trivy image --severity CRITICAL,HIGH --exit-code 1 users-service:dev
trivy image --severity CRITICAL,HIGH --exit-code 1 products-service:dev
trivy image --severity CRITICAL,HIGH --exit-code 1 orders-service:dev
```

### 3. Deploy HashiCorp Vault

```bash
# Option A: Raw manifests (dev mode, fast)
kubectl apply -f k8s/vault/manifests.yaml
kubectl apply -f k8s/vault/secret-vault-root.yaml

# Option B: Helm chart (production-style)
helm repo add hashicorp https://helm.releases.hashicorp.com
helm install vault hashicorp/vault -n vault -f k8s/vault/values.yaml --create-namespace

# Wait for readiness
kubectl wait --for=condition=ready pod -l app=vault -n vault --timeout=180s

# Verify
kubectl exec -n vault deploy/vault -- vault status
# Output: Sealed: false, Initialized: true
```

### 4. Seed secrets & verify services

```bash
# After Vault setup job completes, verify secrets are present
kubectl exec -n vault deploy/vault -- vault kv get secret/devops-platform/users-service
kubectl exec -n vault deploy/vault -- vault kv get secret/devops-platform/products-service
kubectl exec -n vault deploy/vault -- vault kv get secret/devops-platform/orders-service

# Check that microservices report vault_configured: true
kubectl port-forward -n devops-platform svc/users-service 8000:80 &
curl -s http://localhost:8000/ | python3 -m json.tool
# Look for: "vault_configured": true
kill %1
```

### 5. Full security validation

```bash
scripts/validate-security.sh
```

## Status - Implementations

Phase 4 is now **implemented**:

- Secret scanning (Gitleaks) — configured in `.gitleaks.toml` and integrated as the first job in `.github/workflows/ci-cd.yml`
- Container vulnerability scanning (Trivy) — configured in `.github/workflows/ci-cd.yml` after the Docker build job
- HashiCorp Vault — K8s manifests in `k8s/vault/manifests.yaml` and Helm values in `k8s/vault/values.yaml`
- KV v2 secret paths — `secret/devops-platform/<service-name>`
- Microservices — `vault_client.py` module fetched at startup, with environment variable fallback
- K8s deployments — all pods pass `VAULT_ADDR`, `SERVICE_NAME` and reference `vault-root-token` secret via `secretKeyRef`
- Validation — `scripts/validate-security.sh` runs all Phase 4 checks

## Verification criteria

- `gitleaks detect` — 0 secrets detected
- `trivy image` — 0 CRITICAL/HIGH vulnerabilities
- `vault status` — `Sealed: false`
- `scripts/validate-security.sh` — all checks pass