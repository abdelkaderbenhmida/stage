#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap / rotate the GHCR pull-credentials Kubernetes Secret out-of-band.
# ─────────────────────────────────────────────────────────────────────────────
# Why this exists:
#   Every service pod pulls `ghcr.io/<owner>/<svc>:commit-<sha>` at deploy
#   time. GHCR packages for a private repo require a token with the
#   `read:packages` scope. That PAT is stored in .env (git-ignored) and this
#   script materializes it into the `ghcr-pull` Secret in the
#   `devops-platform` namespace WITHOUT ever writing the token to the git
#   index, to disk, or to the command-line arguments (where it would show up
#   in `ps` / shell history) — same discipline as bootstrap-vault-secret.sh.
#
# Usage:
#   scripts/bootstrap-ghcr-pull.sh
#
#   Reads GITHUB_OWNER and GHCR_PAT from .env (repo root). Fails with a clear
#   message if GHCR_PAT is missing. Creates/updates the Secret idempotently.
#
#   Flags:
#     -n, --namespace   Override target namespace (default: devops-platform)
#     -d, --dry-run     Print the kubectl command without executing
#     -h, --help        Show this help
#
# Rotation:
#   Update GHCR_PAT in .env and re-run. The Secret is recreated; the per-service
#   Deployments keep referencing it via the <svc>-sa imagePullSecrets, so just
#   `kubectl rollout restart deploy -n devops-platform` to pick it up.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="devops-platform"
DRY_RUN=false
SECRET_NAME="ghcr-pull"
REGISTRY="ghcr.io"

print_usage() {
  sed -n '2,28p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--namespace) NAMESPACE="${2:-}"; shift 2 ;;
    -d|--dry-run)   DRY_RUN=true; shift ;;
    -h|--help)      print_usage; exit 0 ;;
    *)              echo "error: unknown flag $1 (see -h)" >&2; exit 2 ;;
  esac
done

if [[ -z "${NAMESPACE:-}" ]]; then
  echo "error: --namespace requires a value" >&2
  exit 2
fi

# ── Load GITHUB_OWNER + GHCR_PAT from .env ─────────────────────────────────
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found — cp .env.example .env and fill values" >&2
  exit 1
fi

OWNER=""
PAT=""
while IFS='=' read -r key value; do
  key="$(printf '%s' "$key" | tr -d '[:space:]')"
  value="$(printf '%s' "$value" | tr -d '[:space:]')"
  case "$key" in
    GITHUB_OWNER) OWNER="$value" ;;
    GHCR_PAT)     PAT="$value" ;;
  esac
done < "$ENV_FILE"

if [[ -z "$OWNER" ]]; then
  echo "error: GITHUB_OWNER is not set in $ENV_FILE" >&2
  exit 1
fi
if [[ -z "$PAT" ]]; then
  echo "error: GHCR_PAT is not set in $ENV_FILE — add the read:packages token and re-run" >&2
  exit 1
fi

# ── Build the dockerconfigjson without leaking the PAT into argv/history ───
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
printf '{"auths":{"%s":{"auth":"%s"}}}' "$REGISTRY" "$(printf '%s:%s' "$OWNER" "$PAT" | base64 -w0)" > "$TMP"

CMD=("kubectl" "create" "secret" "docker-registry" "$SECRET_NAME"
     "--namespace=$NAMESPACE"
     "--docker-server=$REGISTRY"
     "--from-file=.dockerconfigjson=$TMP"
     "--dry-run=client" "-o" "yaml")
if $DRY_RUN; then
  echo "would run: ${CMD[*]} | kubectl apply -f -"
  echo "dry-run: applying would not print the token"
  exit 0
fi

"${CMD[@]}" | kubectl apply -f - >/dev/null
echo "ok: $SECRET_NAME Secret updated in namespace $NAMESPACE for $OWNER@$REGISTRY (token value not printed)"