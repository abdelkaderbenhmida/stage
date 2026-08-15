#!/usr/bin/env bash
# DevOps Central Platform — render personal / host-specific values into the
# tracked files that embed them, from .env.
#
# Personal identifiers (GitHub owner/repo, GHCR registry, node IPs, ssh user)
# live ONLY in .env (git-ignored). The tracked deploy-config files carry
# __TOKEN__ markers; this script replaces them with the current .env values so
# the tree is ready to deploy (ArgoCD syncs the rendered repoURLs) while the
# committed source form stays free of personal identifiers.
#
# controlplane/platform_ops.py is NOT rendered — it reads .env directly at runtime
# (SSH_USER / NETWORK_CIDR / K8S_MASTER_NAME / MASTER_SSH_TARGET).
#
# Idempotent: rendering with the same .env is a byte-for-byte no-op. Missing
# .env → clear error, no partial writes.
#
# Usage:
#   scripts/render-env.sh          # render tokens → concrete, in place
#   scripts/render-env.sh --check  # dry run: report files still holding tokens
#
# Exit codes: 0 ok · 1 missing/invalid .env

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"
CHECK_ONLY=false
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=true

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found — cp .env.example .env and fill values" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

REQUIRED=(GITHUB_OWNER GITHUB_REPO K8S_MASTER_NAME MASTER_IP WORKER1_IP WORKER2_IP NETWORK_CIDR SSH_USER)
for key in "${REQUIRED[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "error: $key is empty in $ENV_FILE" >&2
    exit 1
  fi
done

# Derived — never edit these in .env.
GITHUB_REPO_URL="https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}.git"
GHCR_REGISTRY="ghcr.io/${GITHUB_OWNER}"

FILES=(
  "${REPO_ROOT}/k8s/argocd/applicationset.yaml"
  "${REPO_ROOT}/k8s/argocd/project.yaml"
  "${REPO_ROOT}"/k8s/argocd/applications/*.yaml
  "${REPO_ROOT}/k8s/apps/chart/values.yaml"
  "${REPO_ROOT}/k8s/monitoring/kubelet/kubelet-scrape.yaml"
  "${REPO_ROOT}/terraform/variables.tf"
  "${REPO_ROOT}/terraform/terraform.tfvars.example"
  "${REPO_ROOT}/ansible/inventory.ini.example"
)

declare -A PAIRS=(
  [__GITHUB_REPO_URL__]="$GITHUB_REPO_URL"
  [__GHCR_REGISTRY__]="$GHCR_REGISTRY"
  [__MASTER_IP__]="$MASTER_IP"
  [__WORKER1_IP__]="$WORKER1_IP"
  [__WORKER2_IP__]="$WORKER2_IP"
  [__NETWORK_CIDR__]="$NETWORK_CIDR"
  [__SSH_USER__]="$SSH_USER"
  [__K8S_MASTER_NAME__]="$K8S_MASTER_NAME"
)

pending=0
for file in "${FILES[@]}"; do
  [[ -f "$file" ]] || continue
  if $CHECK_ONLY; then
    for token in "${!PAIRS[@]}"; do
      if grep -q "$token" "$file"; then
        echo "pending: $file holds $token" >&2
        pending=$((pending + 1))
        break
      fi
    done
    continue
  fi
  for token in "${!PAIRS[@]}"; do
    sed -i "s|${token}|${PAIRS[$token]}|g" "$file"
  done
done

if $CHECK_ONLY; then
  if [[ $pending -eq 0 ]]; then
    echo "ok: no tokens — tree already rendered"
  else
    echo "$pending file(s) still hold tokens — run scripts/render-env.sh" >&2
  fi
  exit 0
fi

echo "rendered tracked config files from .env (${GITHUB_OWNER}/${GITHUB_REPO})"