#!/usr/bin/env bash
# Stand up the local image registry the deployment pipeline pushes to.
#
# deploy_task builds an image, scans it, pushes it to $REGISTRY and then asks
# the cluster to pull it. Without a registry the first two steps succeed and
# the push fails with "dial tcp 127.0.0.1:5000: connect: connection refused",
# so every deployment on the instance ends in "failed" and every tenant
# account looks empty. This script closes that gap.
#
# Two halves, and both are required:
#
#   1. A registry container published on the host, so `docker push` from the
#      control plane reaches it at localhost:5000.
#   2. A containerd mirror on every kind node, so the *cluster* can resolve
#      the same name. Inside a node, "localhost:5000" means that node — not
#      the host — so a bare registry the host can reach is still invisible to
#      the kubelet. The mirror maps it to the registry's address on the kind
#      network.
#
# Idempotent: safe to re-run after a reboot or a cluster recreate.
#
# Env:
#   REGISTRY_NAME   container name          (default kind-registry)
#   REGISTRY_PORT   host port               (default 5000)
#   CLUSTER_NAME    kind cluster to wire up (default devops-platform)
set -euo pipefail

REGISTRY_NAME="${REGISTRY_NAME:-kind-registry}"
REGISTRY_PORT="${REGISTRY_PORT:-5000}"
CLUSTER_NAME="${CLUSTER_NAME:-devops-platform}"

command -v docker >/dev/null || { echo "docker not found on PATH" >&2; exit 1; }

# 1. The registry itself.
if [ "$(docker inspect -f '{{.State.Running}}' "$REGISTRY_NAME" 2>/dev/null || true)" != "true" ]; then
  docker rm -f "$REGISTRY_NAME" >/dev/null 2>&1 || true
  docker run -d --restart=always \
    -p "127.0.0.1:${REGISTRY_PORT}:5000" \
    --name "$REGISTRY_NAME" \
    registry:2 >/dev/null
  echo "started registry ${REGISTRY_NAME} on 127.0.0.1:${REGISTRY_PORT}"
else
  echo "registry ${REGISTRY_NAME} already running"
fi

# 2. Put it on the kind network so the nodes have a route to it at all.
if docker network inspect kind >/dev/null 2>&1; then
  if ! docker network inspect kind -f '{{range .Containers}}{{.Name}} {{end}}' | grep -qw "$REGISTRY_NAME"; then
    docker network connect kind "$REGISTRY_NAME"
    echo "connected ${REGISTRY_NAME} to the kind network"
  fi
else
  echo "no kind network — skipping cluster wiring" >&2
  exit 0
fi

# 3. Teach every node's containerd that localhost:5000 lives on that network.
#    containerd reads certs.d at request time, so no restart is needed.
NODES="$(kind get nodes --name "$CLUSTER_NAME" 2>/dev/null || true)"
[ -n "$NODES" ] || { echo "cluster ${CLUSTER_NAME} not found — registry is up but unwired" >&2; exit 0; }

DIR="/etc/containerd/certs.d/localhost:${REGISTRY_PORT}"
for node in $NODES; do
  docker exec "$node" mkdir -p "$DIR"
  docker exec -i "$node" cp /dev/stdin "${DIR}/hosts.toml" <<EOF
[host."http://${REGISTRY_NAME}:5000"]
  capabilities = ["pull", "resolve"]
EOF
  echo "wired ${node}"
done

echo "registry ready: push to localhost:${REGISTRY_PORT}, cluster pulls via ${REGISTRY_NAME}:5000"
