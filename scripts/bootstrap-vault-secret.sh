#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap / rotate the Vault root-token Kubernetes Secret out-of-band.
# ─────────────────────────────────────────────────────────────────────────────
# Why this exists:
#   The DevOps platform used to ship the Vault dev root token as a plaintext
#   value inside k8s/vault/secret-vault-root.yaml. That is a P0 secret leak.
#   This script creates the `vault-root-token` Secret in the
#   `devops-platform` namespace WITHOUT ever writing the token to the git
#   index, to disk, or to the command-line arguments (where it would show up
#   in `ps` / shell history).
#
# Usage:
#   VAULT_DEV_ROOT_TOKEN="<token>" scripts/bootstrap-vault-secret.sh
#
#   If VAULT_DEV_ROOT_TOKEN is unset, a 32-byte random hex token is generated
#   and printed ONCE to stdout for the operator to save in a password manager.
#
#   Flags:
#     -n, --namespace   Override target namespace (default: devops-platform)
#     -d, --dry-run     Print the kubectl command without executing
#     -h, --help        Show this help
#
# Rotation:
#   Re-run this script with a new token. The old Secret is deleted and recreated.
#   Apps referencing the Secret via secretKeyRef pick up the new value on the
#   next pod restart. Vault dev mode itself must also be restarted with the
#   matching --dev-root-token-id value (see k8s/vault/manifests.yaml).
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

NAMESPACE="devops-platform"
DRY_RUN=false

print_usage() {
  sed -n '2,30p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--namespace) NAMESPACE="$2"; shift 2 ;;
    -d|--dry-run)   DRY_RUN=true; shift ;;
    -h|--help)      print_usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; print_usage; exit 2 ;;
  esac
done

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command '$1' not found in PATH." >&2
    exit 1
  }
}

require_cmd kubectl
require_cmd base64

if [[ -z "${VAULT_DEV_ROOT_TOKEN:-}" ]]; then
  # Generate a 32-byte (256-bit) random hex token.
  VAULT_DEV_ROOT_TOKEN="$(head -c 32 /dev/urandom | base64 | tr -d '+/\n=' | head -c 64)"
  echo "Generated a new random Vault dev root token (save it now — not shown again):"
  echo "  $VAULT_DEV_ROOT_TOKEN"
fi

if [[ -z "$VAULT_DEV_ROOT_TOKEN" ]]; then
  echo "ERROR: VAULT_DEV_ROOT_TOKEN is empty." >&2
  exit 1
fi

# Namespace must already exist (created by k8s/apps/namespace.yaml).
if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  echo "ERROR: namespace '$NAMESPACE' not found. Apply k8s/apps/namespace.yaml first." >&2
  exit 1
fi

# Delete the existing Secret so rotation is idempotent.
if kubectl get secret vault-root-token -n "$NAMESPACE" >/dev/null 2>&1; then
  echo "Existing vault-root-token Secret found — deleting for rotation."
  if $DRY_RUN; then
    echo "kubectl delete secret vault-root-token -n $NAMESPACE"
  else
    kubectl delete secret vault-root-token -n "$NAMESPACE"
  fi
fi

# Create the Secret with the token piped via stdin so it never appears on
# the command line / in `ps`. --dry-run=client -o yaml | kubectl apply -f -
# keeps the value out of argv and off disk.
echo "Creating vault-root-token Secret in namespace '$NAMESPACE'..."

if $DRY_RUN; then
  # Pipe token via stdin to --from-file=- (kubectl reads file content from stdin).
  printf '%s' "$VAULT_DEV_ROOT_TOKEN" | \
    kubectl create secret generic vault-root-token \
      --namespace="$NAMESPACE" \
      --from-file=root-token=/dev/stdin \
      --dry-run=client -o yaml
  echo "(dry-run — Secret not created)"
else
  # Pipe token via stdin to --from-file=- (kubectl reads file content from stdin).
  printf '%s' "$VAULT_DEV_ROOT_TOKEN" | \
    kubectl create secret generic vault-root-token \
      --namespace="$NAMESPACE" \
      --from-file=root-token=/dev/stdin \
      --dry-run=client -o yaml | \
    kubectl apply -f -
  echo "Secret created. Verify with: kubectl get secret vault-root-token -n $NAMESPACE -o jsonpath='{.data.root-token}' | base64 -d"
fi

# Clear the token from the current shell variable to limit exposure window.
unset VAULT_DEV_ROOT_TOKEN
echo "Done. VAULT_DEV_ROOT_TOKEN unset in current shell."
