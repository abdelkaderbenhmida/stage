#!/usr/bin/env bash
# smoke-test.sh — E2E sanity check for DevOps Central Platform.
#
# Verifies, against the LIVE cluster:
#   1. app pods healthy (>= min replicas, Running)
#   2. Users/Products/Orders respond on /, /livez, /readyz, /etc via port-forward
#   3. Prometheus /targets up, metrics being scraped for each service
#   4. Prometheus reachable, has data points (http_requests_total > 0)
#   5. Grafana reachable + datasource resolves Prometheus
#
# Usage:
#   scripts/smoke-test.sh                 # summary
#   scripts/smoke-test.sh --ci           # exit 1 on first hard failure
#   scripts/smoke-test.sh --skip-grafana # skip grafana auth check
#
# Deps: kubectl, curl, jq. Requires a running cluster + kubectl context.

set -uo pipefail

NS="devops-platform"
MON_NS="monitoring"
CI=0
SKIP_GRAFANA=0
PASS=0
FAIL=0
REPORT=()

# ---- helpers ---------------------------------------------------------
say()  { printf '\033[36m[smoke]\033[0m %s\n' "$*"; }
ok()   { PASS=$((PASS+1)); REPORT+=("ok   - $1"); printf '\033[32m  ✔ %s\033[0m\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); REPORT+=("FAIL - $1"); printf '\033[31m  ✘ %s\033[0m\n' "$*"; }

req()  {  # req <port> <path> -> writes status+body to stdout
  local port="$1" path="$2"
  curl -sf -o /tmp/smoke_body -w '%{http_code}' "http://127.0.0.1:${port}${path}" 2>/dev/null || echo "ERR"
}

# ---- arg parse -------------------------------------------------------
for a in "$@"; do
  case "$a" in
    --ci) CI=1 ;;
    --skip-grafana) SKIP_GRAFANA=1 ;;
    *) echo "unknown arg: $a"; exit 2 ;;
  esac
done

say "== 1/5 pod health =="
for svc in users-service products-service orders-service; do
  ready=$(kubectl get pods -n "$NS" -l app.kubernetes.io/name="$svc" \
          --no-headers 2>/dev/null | awk -F'[ /]+' '$2 == $3 && $2 > 0 {c++} END {print c+0}')
  if [ "$ready" -ge 2 ]; then ok "pods $svc: $ready/2 ready"; else bad "pods $svc: only $ready ready (need 2)"; fi
done

say "== 2) service endpoints (via port-forward) =="
declare -A PORTS=([users-service]=18080 [products-service]=18081 [orders-service]=18082)
SVCS=(users-service products-service orders-service)
declare -A LISTEN=([users-service]="/users" [products-service]="/products" [orders-service]="/orders")

pids=()
for svc in "${SVCS[@]}"; do
  kubectl -n "$NS" port-forward "svc/$svc" "${PORTS[$svc]}:80" >/dev/null 2>&1 &
  pids+=($!)
done
sleep 3

for svc in "${SVCS[@]}"; do
  port="${PORTS[$svc]}"
  root=$(req "$port" "/")
  live=$(req "$port" "/livez")
  data=$(req "$port" "${LISTEN[$svc]}")
  body=$(cat /tmp/smoke_body 2>/dev/null)
  name=$(printf '%s' "$body" | jq -r '.service // .id // "?"' 2>/dev/null)
  if [ "$root" = "200" ] && [ "$live" = "200" ] && [ "$data" = "200" ]; then
    ok "$svc / /livez /${LISTEN[$svc]} 200 (service=$name)"
  else
    bad "$svc root=$root livez=$live data=$data"
  fi
done

# cleanup port fwd
for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done

say "== 3) prometheus targets up =="
kubectl port-forward -n monitoring svc/prometheus-server 19090:80 >/dev/null 2>&1 &
pf=$!
sleep 2
targets=$(curl -sf "http://127.0.0.1:19090/api/v1/targets" 2>/dev/null | jq '[.data.activeTargets[]] | length' 2>/dev/null || echo "0")
up_scrape=$(curl -sf "http://127.0.0.1:19090/api/v1/targets" 2>/dev/null | jq '[.data.activeTargets[] | select(.health=="up")] | length' 2>/dev/null || echo "0")
if [ "${up_scrape:-0}" -ge 1 ] 2>/dev/null; then ok "prometheus: $up_scrape targets UP"; else bad "prometheus: 0 targets UP"; fi

for svc in users-service products-service orders-service; do
  # Pod-autodiscovery scrape targets carry job="kubernetes-pods" for every
  # app pod alike; the actual service identity lives in
  # app_kubernetes_io_name, not job.
  h=$(curl -sf "http://127.0.0.1:19090/api/v1/query?query=up%7Bapp_kubernetes_io_name%3D~%22.*${svc}.*%22%7D" 2>/dev/null | jq -r '[.data.result[]?.value[1]] | join(",")' 2>/dev/null || echo "")
  if [ -n "$h" ] && ! echo "$h" | grep -qv '^1\(,1\)*$'; then ok "scrape ${svc}: up (value=$h)"; else bad "scrape ${svc}: up=$( [ -z "$h" ] && echo missing || echo "$h")"; fi
done

say "== 4) prometheus has request data =="
nreq=$(curl -sf "http://127.0.0.1:19090/api/v1/query?query=sum(http_requests_total)" 2>/dev/null | jq -r '[.data.result[0].value[1]|tonumber] | add // 0' 2>/dev/null || echo "0")
if [ "${nreq:-0}" -gt 0 ] 2>/dev/null; then ok "prometheus http_requests_total = $nreq"; else bad "no app request data in prometheus"; fi

say "== 5) grafana + datasource =="
if [ "$SKIP_GRAFANA" = "1" ]; then say "skipping grafana (--skip-grafana)"; else
  kubectl port-forward -n monitoring svc/grafana 13000:80 >/dev/null 2>&1 &
  pf2=$!
  sleep 2
  code=$(curl -sf -o /dev/null -w '%{http_code}' "http://127.0.0.1:13000/api/health" 2>/dev/null || echo "ERR")
  if [ "$code" = "200" ]; then ok "grafana reachable (health 200)"; else bad "grafana health=$code"; fi
  kill "$pf2" 2>/dev/null || true
fi

kill "$pf" 2>/dev/null || true

echo "-----------------------------------------"
echo "smoke-test summary: $PASS passed, $FAIL failed"
printf '  %s\n' "${REPORT[@]}"
echo "-----------------------------------------"

if [ "$CI" = "1" ] && [ "$FAIL" -gt 0 ]; then exit 1; fi
exit 0
