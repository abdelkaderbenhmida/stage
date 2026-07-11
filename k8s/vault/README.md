# Vault configuration for the DevOps Central Platform

## Overview

This directory contains the Kubernetes manifests and Helm values for HashiCorp Vault,
used to manage secrets for the three microservices (users, products, orders) securely.

## Structure

```
k8s/vault/
├── values.yaml       — Helm chart values for hashicorp/vault chart
└── manifests.yaml    — Raw K8s manifests (Namespace, Deployment, Service, ConfigMap, Job)
```

## Deployment

### Option A: Helm install (production-style)

```bash
# Add HashiCorp Helm repo
helm repo add hashicorp https://helm.releases.hashicorp.com
helm repo update

# Install Vault in its own namespace
helm install vault hashicorp/vault \
  -n vault \
  -f k8s/vault/values.yaml \
  --create-namespace

# Wait for Vault pod to be ready
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=vault \
  -n vault --timeout=180s
```

### Option B: Raw manifests (dev mode, fast setup)

```bash
# Apply namespace + deployment + service + setup job
kubectl apply -f k8s/vault/manifests.yaml

# Wait for vault pod
kubectl wait --for=condition=ready pod -l app=vault -n vault --timeout=180s

# Wait for setup job to seed secrets
kubectl wait --for=condition=complete job/vault-setup-job \
  -n vault --timeout=120s
```

## Secret Paths

After setup, Vault contains (KV v2 at `secret/`):

| Service          | Path                                | Keys                         |
|------------------|-------------------------------------|------------------------------|
| users-service    | secret/devops-platform/users-service    | DATABASE_URL, JWT_SECRET_KEY |
| products-service | secret/devops-platform/products-service | DATABASE_URL, API_KEY         |
| orders-service   | secret/devops-platform/orders-service   | DATABASE_URL, PAYMENT_GATEWAY_KEY |

## Verification

```bash
# Vault status — should show: Initialized: true, Sealed: false
kubectl exec -n vault deploy/vault -- vault status

# List secrets for a service
kubectl exec -n vault deploy/vault -- \
  vault kv get secret/devops-platform/users-service
```

## Security note

This setup uses **dev mode** for simplicity:
- Root token is hardcoded (`root-token-change-me`)
- Data is in memory (lost on pod restart)
- Auto-unsealed (no manual unseal keys)

For **production**, switch to:
- Raft storage backend (persistent volumes)
- Manual/auto unseal (KMS, HSM, or Shamir shares)
- Kubernetes auth method (each microservice gets its own token via SA)
- Dynamic secrets (Leased, auto-rotated) instead of static KV
- Secrets injector sidecar or CSI provider
