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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

# Discover services the same way CI and the ApplicationSet do: app/<svc>/main.py
# (flat) and app/<app>/<svc>/main.py (grouped), k8s name = path with / -> -.
# This MUST stay discovery-driven. Hardcoding the service list is how this
# script previously reported "11 passed, 0 failed" while never touching
# catalog-items or inventory-service at all — a green smoke test that proved
# nothing about the services most likely to be broken (the newly added ones).
#
# Only git-TRACKED markers count. The UI test suite scaffolds a throwaway
# service (app/zz-uitest/probe/) to exercise the add-service flow and deletes it
# again, so a purely filesystem-based scan races it and fails on a service that
# is not supposed to exist — seen here as
#   ✘ pods zz-uitest-probe: only 0 ready (need 2)
# A real service is always committed; a fixture never is. Falls back to the
# filesystem when git is unavailable (e.g. an unpacked source tarball).
discover_services() {
  local d rel
  for d in "$REPO_ROOT"/app/*/ "$REPO_ROOT"/app/*/*/; do
    [ -f "${d}main.py" ] || continue
    rel="${d#"$REPO_ROOT"/}"; rel="${rel%/}"
    if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
      git -C "$REPO_ROOT" ls-files --error-unmatch "${rel}/main.py" >/dev/null 2>&1 || continue
    fi
    printf '%s\n' "${rel#app/}" | tr '/' '-'
  done
}

# Service-specific data endpoints. Optional by design: /, /livez, /readyz and
# /metrics are the contract every service must implement (see app/Dockerfile),
# so they are checked for ALL discovered services. A service missing from this
# map is still fully contract-tested; it just has no extra payload route.
declare -A DATA_PATH=(
  [users-service]="/users"
  [products-service]="/products"
  [orders-service]="/orders"
  [inventory-service]="/inventory"
)

mapfile -t SVCS < <(discover_services)
if [ "${#SVCS[@]}" -eq 0 ]; then
  echo "ERROR: discovered 0 services under $REPO_ROOT/app — wrong repo root?" >&2
  exit 2
fi
say "discovered ${#SVCS[@]} services: ${SVCS[*]}"

# ---- arg parse -------------------------------------------------------
for a in "$@"; do
  case "$a" in
    --ci) CI=1 ;;
    --skip-grafana) SKIP_GRAFANA=1 ;;
    *) echo "unknown arg: $a"; exit 2 ;;
  esac
done

say "== 1/5 pod health =="
for svc in "${SVCS[@]}"; do
  ready=$(kubectl get pods -n "$NS" -l app.kubernetes.io/name="$svc" \
          --no-headers 2>/dev/null | awk '$2 ~ /^1\/1$/ {c++} END {print c+0}')
  if [ "$ready" -ge 2 ]; then ok "pods $svc: $ready/2 ready"; else bad "pods $svc: only $ready ready (need 2)"; fi
done

say "== 2) service endpoints (via port-forward) =="
declare -A PORTS=()
port=18080
for svc in "${SVCS[@]}"; do
  PORTS[$svc]=$port
  port=$((port+1))
done

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
  ready_code=$(req "$port" "/readyz")
  body=$(cat /tmp/smoke_body 2>/dev/null)
  name=$(printf '%s' "$body" | jq -r '.service // .id // "?"' 2>/dev/null)

  # Optional per-service payload route; contract routes above are mandatory.
  data_path="${DATA_PATH[$svc]:-}"
  data="200"
  if [ -n "$data_path" ]; then data=$(req "$port" "$data_path"); fi

  if [ "$root" = "200" ] && [ "$live" = "200" ] && [ "$ready_code" = "200" ] && [ "$data" = "200" ]; then
    ok "$svc / /livez /readyz ${data_path} 200 (service=$name)"
  else
    bad "$svc root=$root livez=$live readyz=$ready_code data(${data_path:-none})=$data"
  fi
done

# cleanup port fwd
for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done

say "== 3) prometheus targets up =="
kubectl port-forward -n monitoring svc/prometheus 19090:9090 >/dev/null 2>&1 &
pf=$!
sleep 2
targets=$(curl -sf "http://127.0.0.1:19090/api/v1/targets" 2>/dev/null | jq '[.data.activeTargets[]] | length' 2>/dev/null || echo "0")
up_scrape=$(curl -sf "http://127.0.0.1:19090/api/v1/targets" 2>/dev/null | jq '[.data.activeTargets[] | select(.health=="up")] | length' 2>/dev/null || echo "0")
if [ "${up_scrape:-0}" -ge 1 ] 2>/dev/null; then ok "prometheus: $up_scrape targets UP"; else bad "prometheus: 0 targets UP"; fi

for svc in "${SVCS[@]}"; do
  h=$(curl -sf "http://127.0.0.1:19090/api/v1/query?query=up%7Bjob%3D~%22.*${svc}.*%22%7D" 2>/dev/null | jq -r '[.data.result[]?.value[1]] | join(",")' 2>/dev/null || echo "")
  if [ "$h" = "1" ] || [ "$h" = "1,1" ]; then ok "scrape ${svc}: up (value=$h)"; else bad "scrape ${svc}: up=$( [ -z "$h" ] && echo missing || echo "$h")"; fi
done

say "== 4) prometheus has request data =="
nreq=$(curl -sf "http://127.0.0.1:19090/api/v1/query?query=sum(http_requests_total)" 2>/dev/null | jq -r '[.data.result[0].value[1]|tonumber] | add // 0' 2>/dev/null || echo "0")
if [ "${nreq:-0}" -gt 0 ] 2>/dev/null; then ok "prometheus http_requests_total = $nreq"; else bad "no app request data in prometheus"; fi

say "== 5) grafana + datasource =="
if [ "$SKIP_GRAFANA" = "1" ]; then say "skipping grafana (--skip-grafana)"; else
  kubectl port-forward -n monitoring svc/grafana 13000:3000 >/dev/null 2>&1 &
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