#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap / rotate the Grafana admin credentials Secret out-of-band.
# ─────────────────────────────────────────────────────────────────────────────
# Why this exists:
#   values.yaml previously shipped adminPassword as a literal placeholder
#   string ("INJECT-VIA-HELM-SET-OR-SECRET-DO-NOT-COMMIT"). As long as a
#   values-nesting bug kept the whole values file from ever reaching the
#   grafana subchart, that placeholder was harmless — but once fixed, it
#   would BE the real, committed, publicly-known admin password. This
#   script creates a `grafana-admin-credentials` Secret out-of-band instead,
#   referenced via the chart's `admin.existingSecret` (see values.yaml).
#
# Usage:
#   GRAFANA_ADMIN_PASSWORD="<password>" scripts/bootstrap-grafana-secret.sh
#
#   If GRAFANA_ADMIN_PASSWORD is unset, a 32-byte random hex password is
#   generated and printed ONCE to stdout for the operator to save.
#
#   Flags:
#     -n, --namespace   Override target namespace (default: monitoring)
#     -d, --dry-run     Print the kubectl command without executing
#     -h, --help        Show this help
#
# Rotation:
#   Re-run this script with a new password, then:
#     kubectl rollout restart deploy grafana -n monitoring
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

NAMESPACE="monitoring"
DRY_RUN=false

print_usage() {
  sed -n '2,26p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--namespace) NAMESPACE="${2:?--namespace requires a value}" ; shift 2 ;;
    -d|--dry-run) DRY_RUN=true ; shift ;;
    -h|--help) print_usage ; exit 0 ;;
    *) echo "Unknown argument: $1" >&2 ; print_usage ; exit 1 ;;
  esac
done

if [[ -z "${GRAFANA_ADMIN_PASSWORD:-}" ]]; then
  GRAFANA_ADMIN_PASSWORD="$(openssl rand -hex 16)"
  echo "Generated a new random Grafana admin password (save it now — not shown again):"
  echo "  $GRAFANA_ADMIN_PASSWORD"
fi

if [[ "$GRAFANA_ADMIN_PASSWORD" == *DO-NOT-COMMIT* ]]; then
  echo "Refusing to use a value containing DO-NOT-COMMIT as the real password." >&2
  exit 1
fi

if $DRY_RUN; then
  echo "kubectl -n \"$NAMESPACE\" create secret generic grafana-admin-credentials \
    --from-literal=admin-user=admin --from-literal=admin-password=\"<REDACTED>\" \
    --dry-run=client -o yaml | kubectl apply -f -"
  exit 0
fi

tmpfile=$(mktemp /tmp/grafana-secret.XXXXXX.yaml)
trap 'rm -f -- "$tmpfile"' EXIT
{
  printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: "grafana-admin-credentials"\n  namespace: "%s"\ntype: Opaque\nstringData:\n  admin-user: "admin"\n  admin-password: "%s"\n' \
    "$NAMESPACE" "$GRAFANA_ADMIN_PASSWORD"
} > "$tmpfile"
kubectl -n "$NAMESPACE" apply -f "$tmpfile" >/dev/null

echo "OK — created/updated Secret grafana-admin-credentials in namespace $NAMESPACE."
echo "Restart Grafana to load it:"
echo "   kubectl rollout restart deploy grafana -n $NAMESPACE"
