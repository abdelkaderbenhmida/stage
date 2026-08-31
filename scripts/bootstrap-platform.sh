#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Single entrypoint for every out-of-band secret this platform needs.
# ─────────────────────────────────────────────────────────────────────────────
# Why this exists:
#   Vault, Elasticsearch/Kibana, and Grafana each need a Secret created
#   out-of-band (no credentials ever committed to git) before their
#   Deployments/StatefulSets can actually start — see
#   scripts/bootstrap-vault-secret.sh, scripts/bootstrap-elasticsearch-secret.sh,
#   and scripts/bootstrap-grafana-secret.sh. On a from-scratch cluster this is
#   3 scripts to remember, in a specific order, plus manifest applies and
#   restarts in between — exactly the kind of thing that gets forgotten and
#   then costs an hour of debugging "why is this pod not starting". This
#   script does all of it in the right order, once.
#
# Usage:
#   scripts/bootstrap-platform.sh                # full run, generates random secrets
#   scripts/bootstrap-platform.sh --dry-run       # print what would happen
#   scripts/bootstrap-platform.sh --skip-vault
#   scripts/bootstrap-platform.sh --skip-elk
#   scripts/bootstrap-platform.sh --skip-grafana
#
# Idempotent: safe to re-run. Existing Secrets get overwritten with fresh
# random values unless the relevant *_PASSWORD / *_TOKEN env var is already
# set when you invoke it.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=false
DO_VAULT=true
DO_ELK=true
DO_GRAFANA=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ; shift ;;
    --skip-vault) DO_VAULT=false ; shift ;;
    --skip-elk) DO_ELK=false ; shift ;;
    --skip-grafana) DO_GRAFANA=false ; shift ;;
    -h|--help) sed -n '2,25p' "$0" ; exit 0 ;;
    *) echo "Unknown argument: $1" >&2 ; exit 1 ;;
  esac
done

say() { printf '\033[36m[bootstrap]\033[0m %s\n' "$*"; }
run() { if $DRY_RUN; then echo "+ $*"; else "$@"; fi; }

say "=== 1/3: Vault ==="
if $DO_VAULT; then
  run kubectl create namespace vault --dry-run=client -o yaml | { $DRY_RUN && cat || kubectl apply -f -; }
  run bash "$SCRIPT_DIR/bootstrap-vault-secret.sh"
  # secretKeyRef only reads same-namespace Secrets — mirror into vault ns too.
  if ! $DRY_RUN; then
    TOKEN=$(kubectl get secret vault-root-token -n devops-platform -o jsonpath='{.data.root-token}' | base64 -d)
    kubectl create secret generic vault-root-token -n vault \
      --from-literal=root-token="$TOKEN" --dry-run=client -o yaml | kubectl apply -f -
  fi
  run kubectl apply -f "$SCRIPT_DIR/../k8s/vault/manifests.yaml"
  if ! $DRY_RUN; then
    # The token just rotated, but `kubectl apply` sees no Deployment spec
    # diff (it references the Secret by name, not value) — the already-
    # running vault process still has the OLD token loaded in-process.
    # Force a restart so it picks up the new one before setup-job tries to
    # authenticate with it.
    say "restarting vault to pick up rotated token..."
    kubectl rollout restart deploy vault -n vault
    kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=vault -n vault --timeout=120s
    kubectl delete job vault-setup-job -n vault --ignore-not-found
    kubectl apply -f "$SCRIPT_DIR/../k8s/vault/manifests.yaml"
    kubectl wait --for=condition=complete job/vault-setup-job -n vault --timeout=120s
  fi
else
  say "skipped (--skip-vault)"
fi

say "=== 2/3: Elasticsearch / Kibana ==="
if $DO_ELK; then
  # Unlike the vault/grafana scripts, this one requires explicit passwords
  # rather than self-generating — supply random ones if the caller hasn't.
  export ELASTIC_PASSWORD="${ELASTIC_PASSWORD:-$(openssl rand -hex 16)}"
  export KIBANA_PASSWORD="${KIBANA_PASSWORD:-$(openssl rand -hex 16)}"
  run bash "$SCRIPT_DIR/bootstrap-elasticsearch-secret.sh"
  if ! $DRY_RUN; then
    say "restarting kibana/logstash/elasticsearch to pick up credentials..."
    kubectl rollout restart deploy kibana -n monitoring 2>/dev/null || true
    kubectl rollout restart deploy logstash -n monitoring 2>/dev/null || true
    kubectl rollout restart sts elasticsearch -n monitoring 2>/dev/null || true
  fi
else
  say "skipped (--skip-elk)"
fi

say "=== 3/3: Grafana ==="
if $DO_GRAFANA; then
  run bash "$SCRIPT_DIR/bootstrap-grafana-secret.sh"
  if ! $DRY_RUN; then
    say "restarting grafana to pick up credentials..."
    kubectl rollout restart deploy grafana -n monitoring 2>/dev/null || true
  fi
else
  say "skipped (--skip-grafana)"
fi

say "Done. Vault KV paths (secret/devops-platform/{users,products,orders}-service)"
say "are still empty by design — inject real per-service secrets separately"
say "(vault kv put ...), the apps fail-closed until you do."
