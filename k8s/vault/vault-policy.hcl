# Vault policy for the DevOps Central Platform microservices.
#
# Spec ref: arborescence.md Phase 4 — k8s/vault/vault-policy.hcl
#
# This HCL defines the least-privilege ACL each microservice ServiceAccount
# gets bound to. Two capabilities only:
#   - read  → fetch the secret value (KV v2 at path secret/data/...)
#   - list  → enumerate keys (KV v2 metadata path secret/metadata/...)
#
# Apply with:
#   vault policy write devops-platform-<svc> k8s/vault/vault-policy.hcl
# then bind via Kubernetes auth:
#   vault write auth/kubernetes/role/<svc> \
#     bound_service_account_names=<svc>-sa \
#     bound_service_account_namespaces=devops-platform \
#     policies=devops-platform-<svc> ttl=1h

# Read the KV v2 secret value for the service's own secret path.
# `${svc}` must be substituted at apply time — one service per discovered app dir.
path "secret/data/devops-platform/${svc}" {
  capabilities = ["read"]
}

# Enumerate the secret metadata (versions, deletion status) so the client
# can pin a revision and decide on a refresh strategy.
path "secret/metadata/devops-platform/${svc}" {
  capabilities = ["read", "list"]
}

# KV v2 delete capability is intentionally NOT granted to the application
# ServiceAccount — applications must never delete their own secrets; removal
# is an operator-only action performed out-of-band via the Vault CLI / UI.
