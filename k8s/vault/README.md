# Vault configuration — DevOps Central Platform

> Significant updates in this pass — see `devops-analysis-report.md` (P0 #1) for
> the audit findings that drove them.

## Overview

HashiCorp Vault manages per-service secrets for `users-service`,
`products-service`, `orders-service`. Each service reads from its own KV v2
path under `secret/devops-platform/<service>`. Vault itself is deployed in
`vault` namespace.

## Structure

```
k8s/vault/
├── manifests.yaml           — Raw K8s manifests (Namespace, Deployment, Service,
│                              ConfigMap + setup Job, RBAC, vault-sa binding)
├── secret-vault-root.yaml   — TEMPLATE only; real token injected out-of-band
├── values.yaml              — Helm values for hashicorp/vault chart (alternative path)
└── README.md
```

## Secret bootstrap (P0 fix)

**The Vault dev root token is NOT committed anywhere in the repo anymore.**
The previous `root-token-change-me` placeholder was deleted from every file it
appeared in (`secret-vault-root.yaml`, `manifests.yaml`, `values.yaml`) and
replaced with environment/S Secret substitution. A ValueError-style install
will fail loudly rather than silently starting Vault with a known token.

### Bootstrapping the Secret out-of-band

Use `scripts/bootstrap-vault-secret.sh` — it:

1. generates a random 64-char hex token (if `VAULT_DEV_ROOT_TOKEN` is unset),
2. creates the `vault-root-token` Secret in cluster with the value piped via
   stdin so the token never appears in `ps` / shell history / disk,
3. rotates by deleting + recreating the Secret idempotently.

```bash
VAULT_DEV_ROOT_TOKEN="<random>" scripts/bootstrap-vault-secret.sh
# or let it generate:
scripts/bootstrap-vault-secret.sh
```

The Vault dev server is configured to read its root token from the
`VAULT_DEV_ROOT_TOKEN_ID` env var, sourced from this Secret via
`secretKeyRef`. The setup Job uses the same mechanism.

## Deployment

### Option A: Helm install (production-style)

```bash
# Add HashiCorp Helm repo
helm repo add hashicorp https://helm.releases.hashicorp.com
helm repo update

# Resolve the root token from the bootstrapped Secret.
ROOT_TOKEN=$(kubectl get secret vault-root-token -n devops-platform \
  -o jsonpath='{.data.root-token}' | base64 -d)

helm install vault hashicorp/vault \
  -n vault \
  -f k8s/vault/values.yaml \
  --set server.dev.devRootToken="${ROOT_TOKEN}" \
  --create-namespace

kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=vault \
  -n vault --timeout=180s
```

### Option B: Raw manifests

```bash
# Apply namespace + deployment + service + setup job + RBAC
kubectl apply -f k8s/vault/manifests.yaml

kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=vault -n vault --timeout=180s

kubectl wait --for=condition=complete job/vault-setup-job \
  -n vault --timeout=120s
```

## Secret paths

After setup, Vault contains (KV v2 at `secret/`):

| Service          | Path                                       | Keys                              |
|------------------|--------------------------------------------|-----------------------------------|
| users-service    | `secret/devops-platform/users-service`     | DATABASE_URL, JWT_SECRET_KEY      |
| products-service | `secret/devops-platform/products-service`  | DATABASE_URL, API_KEY             |
| orders-service   | `secret/devops-platform/orders-service`    | DATABASE_URL, PAYMENT_GATEWAY_KEY |

## Kubernetes auth method

The `vault-setup` Job (in `manifests.yaml`) automatically:

1. enables the `kubernetes` auth method,
2. configures it to talk to the in-cluster API at
   `https://kubernetes.default.svc.cluster.local`,
3. creates a per-service Vault policy `devops-platform-<svc>` with read-only
   access to `secret/data/devops-platform/<svc>`,
4. binds each app ServiceAccount (`<svc>-sa`, namespace `devops-platform`)
   to the matching policy via a Vault role with 1h TTL.

To use it from the apps, replace the `VAULT_TOKEN` env in each Deployment
(`k8s/apps/*-deployment.yaml`) with the Vault Agent Injector annotations
(enabled via `k8s/vault/values.yaml → injector.enabled: true`). See:

- HashiCorp Vault - Kubernetes Auth Method
- Vault Agent Injector tutorial

## Verification

```bash
# Vault status — should be initialized=true, sealed=false.
kubectl exec -n vault deploy/vault -- vault status

# List secrets for a service.
kubectl exec -n vault deploy/vault -- \
  vault kv get secret/devops-platform/users-service

# End-to-end token check (the new contract — runs validate-security.sh #4):
scripts/validate-security.sh
```

## Security posture (changes from audit)

- **Root token out of git** — rotated via `scripts/bootstrap-vault-secret.sh`.
- **Per-service SAs** — each discovered app service gets its own
  `<service>-sa` ServiceAccount (rendered by `k8s/apps/chart`); the
  `vault-setup` job derives services from the `devops-service-list`
  ConfigMap synced by CI, so no service name is hardcoded.
- **Fail-closed `optional: false`** on the `VAULT_TOKEN` `secretKeyRef` — pods
  refuse to start when the Secret is absent/empty.
- **Restricted Pod Security Admission** label on both namespaces enforces
  non-root, seccomp+RuntimeDefault, no priv-escalation, drop ALL caps.
- **NetworkPolicies** in `devops-platform` allow-list app→vault:8200,
  app→dns, app→postgres, prometheus-scrape, intra-namespace only (`k8s/apps/networkpolicies.yaml`).

## Production hardening (still TODO when promoting out of dev)

- Raft storage backend (persistent volumes) — dev mode loses state on restart.
- Manual/auto unseal (KMS, HSM, or Shamir shares).
- TLS for Vault API (`https://...`) — currently HTTP in-cluster.
- Drop the `vault-root-token` Secret entirely; use Vault Agent Injector +
  Kubernetes auth with short-lived, auto-rotated tokens per app.
