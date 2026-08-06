#!/usr/bin/env bash
# stress-hpa.sh — load test + autoscaling verification for DevOps Central Platform.
#
#   1. Baseline HPA state (replicas, cpu%, mem%)
#   2. Port-forward users-service + prometheus
#   3. Push sustained load with `ab` (apache bench)
#   4. Watch HPA — expect replica count to scale from min (2) up toward max (5)
#   5. Verify request metrics spiked in Prometheus (http_requests_total)
#   6. Optional live tail of the HPA (scale-up window 30s)
#
# Usage:
#   scripts/stress-hpa.sh                        # default load + 180s watch
#   scripts/stress-hpa.sh -c 200 -n 30000        # custom ab concurrency / total reqs
#   scripts/stress-hpa.sh --watch 240            # tail HPA for N seconds
#   scripts/stress-hpa.sh --no-watch             # skip the live tail
#   scripts/stress-hpa.sh --ci                    # gating (exit 1 if no scale-up seen)
#
# Deps: kubectl, curl, jq, ab (apache2-utils). Needs running cluster + kubectl.

set -uo pipefail

NS="devops-platform"
MON_NS="monitoring"
SVC="users-service"
HPA="$SVC"
AB_C=80
AB_N=4000
WATCH_SEC=90
DO_WATCH=1
CI=0
PIDS=()

say() { printf '\033[36m[stress]\033[0m %s\n' "$*"; }

# ---- arg parse -------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    -c)         AB_C=$2; shift 2 ;;
    -n)         AB_N=$2; shift 2 ;;
    --watch)    WATCH_SEC=$2; shift 2 ;;
    --no-watch|--skip-watch) DO_WATCH=0; shift ;;
    --ci)       CI=1; shift ;;
    -h|--help)  grep '^#' "$0" | sed 's/^# //'; exit 0 ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done

cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

say "baseline HPA:"
kubectl -n "$NS" get hpa "$HPA"

say "port-forward users-service:80 -> 18080"
kubectl -n "$NS" port-forward "svc/$SVC" 18080:80 >/dev/null 2>&1 & PIDS+=("$!")
say "port-forward prometheus:9090 -> 19090"
kubectl -n "$MON_NS" port-forward "svc/prometheus" 19090:9090 >/dev/null 2>&1 & PIDS+=("$!")
sleep 3

code=$(curl -sf -o /dev/null -w '%{http_code}' "http://127.0.0.1:18080/livez" || echo ERR)
say "service /livez -> $code (expect 200)"

q_before=$(curl -sf "http://127.0.0.1:19090/api/v1/query?query=sum(http_requests_total%7Bservice%3D%22$SVC%22%7D)" 2>/dev/null \
  | jq -r '.data.result[0].value[1] // 0' || echo 0)
say "prometheus reqs before load (total): $q_before"

replicas_before=$(kubectl -n "$NS" get hpa "$HPA" -o jsonpath='{.status.currentReplicas}')
say "replicas before load: $replicas_before"

say "running ab: -k -c $AB_C -n $AB_N against /users"
ab_out=$(ab -k -c "$AB_C" -n "$AB_N" "http://127.0.0.1:18080/users" 2>&1) || {
  echo "ab failed — is apache2-utils installed?"; echo "$ab_out" | head -5; exit 1; }
printf '%s\n' "$ab_out" | grep -E "Requests per second|Failed requests|Complete requests|Time per request" | sed 's/^/  /'

if [ "$DO_WATCH" = "1" ]; then
  # HPA needs >70% CPU sustained across its 30s window. Keep a steady trickle
  # of load running in the background for the whole watch so CPU stays elevated
  # (single ab burst finishes too fast to register a scale-up).
  say "keeping sustained load for ${WATCH_SEC}s while watching HPA..."
  ( end=$((SECONDS+WATCH_SEC)); \
    while [ $SECONDS -lt $end ]; do \
      ab -k -c "$AB_C" -n 2000 "http://127.0.0.1:18080/users" >/dev/null 2>&1; \
    done ) &
  LOAD_PID=$!
  say "HPA tail for ${WATCH_SEC}s — watch replicas scale from $replicas_before:"
  ( timeout "$WATCH_SEC" kubectl -n "$NS" get hpa "$HPA" -w ) || true
  kill "$LOAD_PID" 2>/dev/null || true
else
  say "sleeping 5s for metrics propagation, then re-checking HPA..."
  sleep 5
  kubectl -n "$NS" get hpa "$HPA"
fi

q_after=$(curl -sf "http://127.0.0.1:19090/api/v1/query?query=sum(http_requests_total%7Bservice%3D%22$SVC%22%7D)" 2>/dev/null \
  | jq -r '.data.result[0].value[1] // 0' || echo 0)
replicas_after=$(kubectl -n "$NS" get hpa "$HPA" -o jsonpath='{.status.currentReplicas}' 2>/dev/null)

say "prometheus reqs after load: $q_after  (before: $q_before)"
say "replicas after load: $replicas_after  (before: $replicas_before)"

echo "-----------------------------------------"
if [ "${replicas_after:-0}" -gt "${replicas_before:-0}" ] 2>/dev/null; then
  say "PASS: HPA scaled ${replicas_before} -> ${replicas_after} replicas; prometheus reqs ${q_before} -> ${q_after}."
  if [ "$CI" = "1" ]; then exit 0; fi
else
  say "NOTE: replicas still ${replicas_after}. HPA scale-up window=30s; may need higher/event load."
  say "      re-run: scripts/stress-hpa.sh -c 300 -n 20000 --watch 240"
  if [ "$CI" = "1" ]; then exit 1; fi
fi
echo "-----------------------------------------"
exit 0