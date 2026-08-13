#!/usr/bin/env bash
# Provision a new worker VM: bump worker_count → terraform apply →
# regenerate inventory → ansible join (docker, k8s, worker roles).
#
# Non-interactive (UI-drivable via the SCRIPTS runner). Auto-derives the
# next worker index from worker_count in terraform.tfvars — no args needed.
#
# Refuses (exit 1) when the host cannot honestly support the VM: free disk
# below disk_size_gb. The UI also runs node_preflight() before offering this.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${REPO_ROOT}/terraform"

COUNT="$(grep -E '^\s*worker_count\s*=' "${TF_DIR}/terraform.tfvars" | grep -oE '[0-9]+' | head -1)"
COUNT="${COUNT:-2}"
NEW=$((COUNT + 1))
HOST="$(printf 'worker-%02d' "${NEW}")"
DISK_GB="$(grep -E '^\s*disk_size_gb\s*=' "${TF_DIR}/terraform.tfvars" | grep -oE '[0-9]+' | head -1)"
DISK_GB="${DISK_GB:-20}"

echo "→ adding ${HOST} (worker_count ${COUNT} → ${NEW})"

FREE_KB="$(df -Pk /var/lib/libvirt/images 2>/dev/null | awk 'NR==2{print $4}')"
if [[ -z "${FREE_KB}" ]]; then
  FREE_KB="$(df -Pk / | awk 'NR==2{print $4}')"
fi
FREE_GB=$((FREE_KB / 1024 / 1024))
echo "  host free disk: ${FREE_GB}GB (requested disk_size_gb: ${DISK_GB}GB)"
if [[ "${FREE_GB}" -lt "${DISK_GB}" ]]; then
  echo "✕ REFUSING: host free disk ${FREE_GB}GB < requested disk_size_gb ${DISK_GB}GB" >&2
  exit 1
fi

sed -i "s/^worker_count.*/worker_count = ${NEW}/" "${TF_DIR}/terraform.tfvars"

cd "${TF_DIR}"
terraform apply -input=false -auto-approve

"${REPO_ROOT}/scripts/generate-inventory.sh"

cd "${REPO_ROOT}"
ansible-playbook ansible/playbook.yml --limit "${HOST}" --tags docker,k8s,worker

echo "✓ ${HOST} joined the cluster — run 'kubectl get nodes' to confirm"
