#!/usr/bin/env bash
# DevOps Central Platform — Regenerate the Ansible inventory from Terraform output.
#
# Spec ref: arborescence.md → scripts/generate-inventory.sh
#
# Terraform already renders terraform/inventory.ini via the local_file
# resource in main.tf + inventory.tpl. This script is a convenience wrapper
# that triggers a `terraform refresh` + `terraform output` so the inventory
# stays in sync after every apply/destroy, without needing to remember the
# exact terraform invocation.
#
# Usage:
#   scripts/generate-inventory.sh                 # default
#   scripts/generate-inventory.sh --no-refresh    # skip terraform refresh
#   TF_DIR=../terraform scripts/generate-inventory.sh
#
# Exit codes: 0 ok · 1 terraform failure · 2 usage error

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${TF_DIR:-${REPO_ROOT}/terraform}"
TARGET="${REPO_ROOT}/terraform/inventory.ini"
REFRESH=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-refresh) REFRESH=false ; shift ;;
    -h|--help)    sed -n '2,22p' "$0" ; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2 ; exit 2 ;;
  esac
done

if [[ ! -d "${TF_DIR}" ]]; then
  echo "ERROR: terraform dir not found: ${TF_DIR}" >&2
  exit 2
fi

cd "${TF_DIR}"

if [[ "${REFRESH}" == "true" ]]; then
  echo "→ terraform refresh (sync state with real resources)..."
  terraform refresh -input=false
fi

# The local_file.ansible_inventory resource already writes inventory.ini.
# Trigger it via a targeted apply (cheap — single resource, no external diff).
echo "→ terraform apply -target local_file.ansible_inventory..."
terraform apply -input=false -auto-approve -target local_file.ansible_inventory

if [[ -f "${TARGET}" ]]; then
  echo "✓ Ansible inventory written to: ${TARGET}"
  echo "  master:  $(awk '/^\[masters\]/{f=1; next} /^\[/{f=0} f && NF {print; exit}' "${TARGET}")"
  echo "  workers: $(awk '/^\[workers\]/{f=1; next} /^\[/{f=0} f && NF' "${TARGET}" | wc -l)"
else
  echo "ERROR: ${TARGET} not generated" >&2
  exit 1
fi
