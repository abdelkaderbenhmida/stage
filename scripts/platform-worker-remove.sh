#!/usr/bin/env bash
# Remove the LAST worker VM: kubectl drain (daemonsets preserved) →
# kubectl delete node → lower worker_count → terraform apply. Order matters —
# reversed, terraform would orphan a NotReady node.
#
# Non-interactive (UI-drivable via the SCRIPTS runner). Auto-derives the
# worker to remove as worker-%02d of the current worker_count.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${REPO_ROOT}/terraform"

COUNT="$(grep -E '^\s*worker_count\s*=' "${TF_DIR}/terraform.tfvars" | grep -oE '[0-9]+' | head -1)"
COUNT="${COUNT:-2}"
if [[ "${COUNT}" -le 1 ]]; then
  echo "✕ REFUSING: worker_count ${COUNT} — at least 1 worker must remain" >&2
  exit 1
fi
HOST="$(printf 'worker-%02d' "${COUNT}")"
NEW=$((COUNT - 1))

echo "→ removing ${HOST} (worker_count ${COUNT} → ${NEW})"

kubectl drain "${HOST}" --ignore-daemonsets --delete-emptydir-data --force
kubectl delete node "${HOST}"

sed -i "s/^worker_count.*/worker_count = ${NEW}/" "${TF_DIR}/terraform.tfvars"

cd "${TF_DIR}"
terraform apply -input=false -auto-approve

"${REPO_ROOT}/scripts/generate-inventory.sh"

echo "✓ ${HOST} drained, deleted from the cluster, and removed from terraform state"
