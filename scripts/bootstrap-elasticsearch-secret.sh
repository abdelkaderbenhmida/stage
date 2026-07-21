#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap / rotate the Elasticsearch + Kibana credentials out-of-band.
# ─────────────────────────────────────────────────────────────────────────────
# Mirrors scripts/bootstrap-vault-secret.sh: no secret ever lands on disk, in
# git, or in `ps`. Reads passwords from stdin/env, creates two Secrets in the
# `monitoring` namespace:
#   - elasticsearch-credentials  (ELASTIC_PASSWORD, KIBANA_PASSWORD)
#   - logstash-elasticsearch-auth (ELASTIC_PASSWORD — reused)
#   - kibana-credentials          (KIBANA_PASSWORD)
#
# Usage:
#   ELASTIC_PASSWORD="<elastic-pwd>" \
#   KIBANA_PASSWORD="<kibana-pwd>" \
#     scripts/bootstrap-elasticsearch-secret.sh
#
#   # interactive prompt (passwords typed once, captured via `read -s`)
#   scripts/bootstrap-elasticsearch-secret.sh --prompt
#
#   Flags:
#     -n, --namespace   Override target namespace (default: monitoring)
#     -d, --dry-run      Print the kubectl command, do not execute
#     -h, --help         Show this help
#
# Rotation (dev cluster only):
#   1. Rotate `elastic` password via ES API:
#        curl -u elastic:<OLD> -X POST \
#          http://elasticsearch:9200/_security/user/elastic/_password \
#          -H 'Content-Type: application/json' -d '{"password":"<NEW>"}'
#   2. Re-run this script with the NEW password.
#   3. `kubectl rollout restart deploy logstash -n monitoring`
#      `kubectl rollout restart deploy kibana   -n monitoring`
#
# ⚠️ Production: replace this script with SealedSecrets / SOPS / Vault Injection.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

NAMESPACE="monitoring"
DRY_RUN=false
PROMPT=false

print_usage() {
  sed -n '2,38p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) print_usage ; exit 0 ;;
    -n|--namespace) NAMESPACE="${2:?--namespace requires a value}" ; shift 2 ;;
    -d|--dry-run) DRY_RUN=true ; shift ;;
    --prompt) PROMPT=true ; shift ;;
    *) echo "ERROR: unknown argument: $1" >&2 ; exit 2 ;;
  esac
done

if ! command -v kubectl >/dev/null 2>&1; then
  echo "ERROR: kubectl not installed" >&2
  exit 1
fi

# Resolve the two passwords without ever echoing them.
if $PROMPT; then
  read -rsp "ELASTIC_PASSWORD: " ELASTIC_PASSWORD; echo
  read -rsp "KIBANA_PASSWORD:  " KIBANA_PASSWORD;  echo
fi
ELASTIC_PASSWORD="${ELASTIC_PASSWORD:-}"
KIBANA_PASSWORD="${KIBANA_PASSWORD:-}"

if [[ -z "$ELASTIC_PASSWORD" || -z "$KIBANA_PASSWORD" ]]; then
  echo "ERROR: both ELASTIC_PASSWORD and KIBANA_PASSWORD must be set (env vars or --prompt)" >&2
  exit 1
fi
if [[ "$ELASTIC_PASSWORD" == *DO-NOT-COMMIT* || "$KIBANA_PASSWORD" == *DO-NOT-COMMIT* ]]; then
  echo "ERROR: refusing to store the placeholder value" >&2
  exit 1
fi

apply_secret() {
  local name="$1" key="$2" value="$3"
  # Use process substitution so the plaintext name=value never appears on the
  # command-line (which would leak via `ps` to other users).
  if $DRY_RUN; then
    echo "kubectl -n \"$NAMESPACE\" create secret generic \"$name\" \
      --from-literal=$key=\"<REDACTED>\" --dry-run=client -o yaml | kubectl apply -f -"
    return
  fi
  kubectl -n "$NAMESPACE" create secret generic "$name" \
    --from-literal="$key=$value" --dry-run=client -o yaml \
    | kubectl apply -f - >/dev/null
}

# Apply the three Secrets that ELK manifests reference.
apply_secret elasticsearch-credentials       ELASTIC_PASSWORD "$ELASTIC_PASSWORD"
apply_secret elasticsearch-credentials        KIBANA_PASSWORD  "$KIBANA_PASSWORD"
apply_secret logstash-elasticsearch-auth     ELASTIC_PASSWORD "$ELASTIC_PASSWORD"
apply_secret kibana-credentials              KIBANA_PASSWORD  "$KIBANA_PASSWORD"

echo "OK — created/updated Secrets in namespace $NAMESPACE:"
echo "   - elasticsearch-credentials (ELASTIC_PASSWORD + KIBANA_PASSWORD)"
echo "   - logstash-elasticsearch-auth (ELASTIC_PASSWORD — reused)"
echo "   - kibana-credentials (KIBANA_PASSWORD)"
echo ""
echo "Restart consumers to load the new values:"
echo "   kubectl rollout restart deploy kibana   -n $NAMESPACE"
echo "   kubectl rollout restart deploy logstash -n $NAMESPACE"
echo "   kubectl rollout restart sts elasticsearch -n $NAMESPACE"
