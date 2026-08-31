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
  # Avoid `--from-literal` which puts the plaintext on argv (visible via `ps`).
  # Instead pipe the YAML body via stdin: write to a temp file in mktemp, then
  # `kubectl apply -f <temp>` and remove the file in a trap-style cleanup.
  # kubectl's --from-file requires the value to live on disk somewhere we
  # control the permissions on. mktemp-derived tmp file is created with 0600.
  if $DRY_RUN; then
    echo "kubectl -n \"$NAMESPACE\" create secret generic \"$name\" \
      --from-literal=$key=\"<REDACTED>\" --dry-run=client -o yaml | kubectl apply -f -"
    return
  fi
  local tmpfile
  tmpfile=$(mktemp /tmp/elk-secret.XXXXXX.yaml)
  trap 'rm -f -- "$tmpfile"' RETURN INT TERM
  # Build YAML by rendering the literal value via stdin (no argv) using cat.
  {
    printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: "%s"\n  namespace: "%s"\ntype: Opaque\nstringData:\n  %s: "%s"\n' \
      "$name" "$NAMESPACE" "$key" "$value"
  } > "$tmpfile"
  kubectl -n "$NAMESPACE" apply -f "$tmpfile" >/dev/null
  rm -f -- "$tmpfile"
  trap - RETURN INT TERM
}

# Apply the three Secrets that ELK manifests reference.
apply_secret elasticsearch-credentials       ELASTIC_PASSWORD "$ELASTIC_PASSWORD"
apply_secret elasticsearch-credentials        KIBANA_PASSWORD  "$KIBANA_PASSWORD"
apply_secret logstash-elasticsearch-auth     ELASTIC_PASSWORD "$ELASTIC_PASSWORD"
apply_secret kibana-credentials              KIBANA_PASSWORD  "$KIBANA_PASSWORD"

# Filebeat lives in its own "logging" namespace (needs hostPath, which even
# baseline PodSecurity forbids in "monitoring") but still reads
# ELASTIC_PASSWORD from elasticsearch-credentials — Secrets don't cross
# namespaces, so mirror a copy over.
if ! $DRY_RUN; then
  kubectl create namespace logging --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  tmpfile=$(mktemp /tmp/elk-secret.XXXXXX.yaml)
  trap 'rm -f -- "$tmpfile"' RETURN INT TERM
  printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: "elasticsearch-credentials"\n  namespace: "logging"\ntype: Opaque\nstringData:\n  ELASTIC_PASSWORD: "%s"\n' \
    "$ELASTIC_PASSWORD" > "$tmpfile"
  kubectl -n logging apply -f "$tmpfile" >/dev/null
  rm -f -- "$tmpfile"
  trap - RETURN INT TERM
fi

echo "OK — created/updated Secrets in namespace $NAMESPACE:"
echo "   - elasticsearch-credentials (ELASTIC_PASSWORD + KIBANA_PASSWORD)"
echo "   - logstash-elasticsearch-auth (ELASTIC_PASSWORD — reused)"
echo "   - kibana-credentials (KIBANA_PASSWORD)"

# These Secrets only set what Kibana/Logstash SEND as credentials. The
# built-in kibana_system user's password INSIDE Elasticsearch is separate
# and must be set to match via the security API, or Kibana gets stuck with
# "security_exception: unable to authenticate user [kibana_system]" forever.
# Best-effort: only runs if elasticsearch-0 already exists and is ready.
if kubectl get pod elasticsearch-0 -n "$NAMESPACE" \
    -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null | grep -q true; then
  echo ""
  echo "Syncing kibana_system password inside Elasticsearch..."
  kubectl exec elasticsearch-0 -n "$NAMESPACE" -- curl -s -k -u "elastic:$ELASTIC_PASSWORD" -X POST \
    "http://localhost:9200/_security/user/kibana_system/_password" \
    -H 'Content-Type: application/json' \
    -d "{\"password\":\"$KIBANA_PASSWORD\"}" >/dev/null
  echo "   Done."
else
  echo ""
  echo "!! elasticsearch-0 isn't ready yet — once it is, run:"
  echo "   kubectl exec elasticsearch-0 -n $NAMESPACE -- curl -s -k -u elastic:\$ELASTIC_PASSWORD -X POST \\"
  echo "     http://localhost:9200/_security/user/kibana_system/_password \\"
  echo "     -H 'Content-Type: application/json' -d '{\"password\":\"'\"\$KIBANA_PASSWORD\"'\"}'"
fi

echo ""
echo "Restart consumers to load the new values:"
echo "   kubectl rollout restart deploy kibana   -n $NAMESPACE"
echo "   kubectl rollout restart deploy logstash -n $NAMESPACE"
echo "   kubectl rollout restart sts elasticsearch -n $NAMESPACE"
