#!/usr/bin/env bash
# Make the in-cluster observability stack reachable from the control plane.
#
# Prometheus and Loki run inside Kubernetes; the control plane runs on the
# host. `PROMETHEUS_URL` and `LOKI_URL` therefore have to resolve to something
# the host can reach, and on a local cluster that means a port-forward. There
# was no script for it, so the forwards were set up by hand, died with whatever
# shell started them, and the console then reported "Metrics backend
# unavailable" and "Log backend unavailable" with nothing pointing at the
# cause.
#
# This deploys Loki and promtail if they are missing, then holds a forward for
# each backend, restarting it if it drops. Run it in its own terminal and leave
# it there, or under a process supervisor.
#
# Env:
#   PROM_PORT   host port for Prometheus (default 9090)
#   LOKI_PORT   host port for Loki       (default 3100)
#   NAMESPACE   monitoring namespace     (default monitoring)
#
# Then point the control plane at them:
#   export PROMETHEUS_URL=http://127.0.0.1:9090
#   export LOKI_URL=http://127.0.0.1:3100
set -euo pipefail

PROM_PORT="${PROM_PORT:-9090}"
LOKI_PORT="${LOKI_PORT:-3100}"
NAMESPACE="${NAMESPACE:-monitoring}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v kubectl >/dev/null || { echo "kubectl not found on PATH" >&2; exit 1; }
kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || {
  echo "namespace ${NAMESPACE} does not exist — apply k8s/monitoring first" >&2
  exit 1
}

# 1. Loki. Its manifests were missing from git for a long time, so a cluster
#    rebuilt from this repository simply had no log backend.
if ! kubectl get deployment loki -n "$NAMESPACE" >/dev/null 2>&1; then
  echo "loki not deployed — applying k8s/monitoring/loki/"
  kubectl apply -f "${ROOT}/k8s/monitoring/loki/" >/dev/null
fi

echo "waiting for loki to become ready"
kubectl rollout status deployment/loki -n "$NAMESPACE" --timeout=180s || {
  echo "loki did not become ready; forwarding anyway so the failure is visible" >&2
}

# 2. One supervised forward per backend. `kubectl port-forward` exits when the
#    pod it is attached to restarts, which is exactly when someone is looking
#    at the console and wondering why the panels went blank.
forward() {
  local name="$1" service="$2" host_port="$3" target_port="$4"
  while true; do
    kubectl port-forward -n "$NAMESPACE" "svc/${service}" "${host_port}:${target_port}" \
      >/dev/null 2>&1 || true
    echo "[$(date +%H:%M:%S)] ${name} forward dropped — reconnecting" >&2
    sleep 2
  done
}

trap 'kill 0' EXIT INT TERM

forward prometheus prometheus "$PROM_PORT" 9090 &
forward loki loki "$LOKI_PORT" 3100 &

cat <<EOF

forwarding:
  prometheus  http://127.0.0.1:${PROM_PORT}
  loki        http://127.0.0.1:${LOKI_PORT}

Export these for the API and the worker, then restart both:

  export PROMETHEUS_URL=http://127.0.0.1:${PROM_PORT}
  export LOKI_URL=http://127.0.0.1:${LOKI_PORT}

Leave this running. Ctrl-C stops both forwards.
EOF

wait
