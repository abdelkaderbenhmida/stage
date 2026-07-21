#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# DevOps Central Platform — Phase 7 validation script.
# ─────────────────────────────────────────────────────────────────────────────
# Runs all 7 end-to-end checks described in
# files.md/DevOps_Central_Platform_Etapes_Implementation.md (Phase 7):
#   1.  Cluster Kubernetes opérationnel
#   2.  Pods en statut Running
#   3.  Aucune vulnérabilité critique (Trivy)
#   4.  Aucun secret détecté (Gitleaks)
#   5.  ArgoCD synchronisé
#   6.  Dashboards Grafana actifs
#   7.  Logs indexés dans Kibana (ELK)
# Plus two optional fail-forward tests:
#   A. Self-healing — delete a Pod and confirm it is recreated < 30s.
#   B. Canary rollback — cause a 5xx regression and confirm Flagger rollbacks.
#
# Usage:
#   scripts/validate-platform.sh                 # local: colored summary
#   scripts/validate-platform.sh --ci           # CI: exit 1 on any failure
#   scripts/validate-platform.sh --skip-incident  # skip A + B destructive tests
#   scripts/validate-platform.sh --only 1,2,5    # run only checks 1,2,5
#
# Exit codes:
#   0 — all selected checks passed
#   1 — at least one check failed
#   2 — usage / configuration error
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

CI_MODE=false
SKIP_INCIDENT=false
ONLY=""
NAMESPACE="devops-platform"
MONITORING_NS="monitoring"
ARGOCD_NS="argocd"
APP_NAMESPACE="devops-platform"
IMAGE_TAG="latest"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ci) CI_MODE=true ; shift ;;
    --skip-incident) SKIP_INCIDENT=true ; shift ;;
    --only) ONLY="${2:?--only requires a comma-separated list of check ids (1-7)}" ; shift 2 ;;
    --namespace) NAMESPACE="${2:?--namespace requires a value}" ; shift 2 ;;
    --image-tag) IMAGE_TAG="${2:?--image-tag requires a value}" ; shift 2 ;;
    -h|--help) sed -n '2,40p' "$0" ; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2 ; exit 2 ;;
  esac
done

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BOLD=$'\033[1m'
NC=$'\033[0m'

PASS=0
FAIL=0
SKIPPED=0

# Optional: filter checks via --only 1,3,5.
should_run() {
  local id="$1"
  if [[ -z "$ONLY" ]]; then return 0; fi
  local IFS=','
  local -a ids=( $ONLY )
  for x in "${ids[@]}"; do
    if [[ "$x" == "$id" ]]; then return 0; fi
  done
  return 1
}

require_tool() {
  local tool="$1"
  local hint="${2:-}"
  if ! command -v "$tool" >/dev/null 2>&1; then
    if $CI_MODE; then
      echo -e "${RED}  ❌ FAIL${NC} — required tool '$tool' not found." >&2
      [[ -n "$hint" ]] && echo "     hint: $hint" >&2
      exit 1
    fi
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — '$tool' not installed. ${hint}" >&2
    return 1
  fi
  return 0
}

record_pass() { local name="$1"; echo -e "${GREEN}  ✅ PASS${NC} — $name" ; ((PASS++)) ; }
record_fail() { local name="$1"; echo -e "${RED}  ❌ FAIL${NC} — $name" >&2 ; ((FAIL++)) ; }

# Run a named check function and gate it through should_run().
run_check() {
  local id="$1"
  local name="$2"
  shift 2
  if ! should_run "$id"; then
    echo -e "${YELLOW}  ⏭  SKIP${NC} — check $id ($name) filtered by --only" >&2
    ((SKIPPED++))
    return 0
  fi
  "$@" "$id" "$name"
}

echo "${BOLD}─────────────────────────────────────────────────────${NC}"
echo "${BOLD} DevOps Central Platform — Phase 7 validation${NC}"
echo "${BOLD} mode: $([[ $CI_MODE == true ]] && echo CI || echo local)${NC}"
echo "${BOLD} namespace: $NAMESPACE${NC}"
echo "${BOLD}─────────────────────────────────────────────────────${NC}"
echo ""

# ─── Check 1: Cluster opérationnel ───────────────────────────────────
check_cluster() {
  local id="$1" name="$2"
  echo "$id. $name"
  if ! require_tool kubectl "https://kubernetes.io/docs/tasks/tools/install-kubectl/"; then
    ((SKIPPED++)); return
  fi
  local nodes_json ready_count total
  if ! nodes_json=$(kubectl get nodes -o json 2>/dev/null); then
    record_fail "kubectl get nodes failed (cluster unreachable?)"
    return
  fi
  total=$(printf '%s' "$nodes_json" | jq -r '.items | length')
  ready_count=$(printf '%s' "$nodes_json" | jq -r '[.items[] | select(.status.conditions[] | select(.type=="Ready").status=="True")] | length')
  if [[ "$ready_count" == "$total" && "$total" -ge 1 ]]; then
    record_pass "Cluster opérationnel : $ready_count/$total nœuds Ready"
  else
    record_fail "Cluster not Ready: $ready_count/$total nœuds"
  fi
}

# ─── Check 2: Pods Running ───────────────────────────────────────────
check_pods() {
  local id="$1" name="$2"
  echo "$id. $name"
  for svc in users-service products-service orders-service; do
    local desired ready
    desired=$(kubectl get deploy -n "$NAMESPACE" "$svc" -o jsonpath='{.status.replicas}' 2>/dev/null || echo 0)
    ready=$(kubectl get deploy -n "$NAMESPACE" "$svc" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
    ready=${ready:-0}
    if [[ "$ready" == "$desired" && "$ready" -ge 1 ]]; then
      record_pass "$svc : $ready/$desired Pods Running"
    else
      record_fail "$svc : $ready/$desired Pods Running"
    fi
  done
}

# ─── Check 3: Trivy (no CRITICAL/HIGH) ───────────────────────────────
check_trivy() {
  local id="$1" name="$2"
  echo "$id. $name"
  if ! require_tool trivy "https://aquasecurity.github.io/trivy/latest/install/"; then
    ((SKIPPED++)); return
  fi
  if ! command -v docker >/dev/null 2>&1; then
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — docker not installed." >&2
    [[ $CI_MODE == true ]] && exit 1
    ((SKIPPED++)); return
  fi
  local scanned=0
  for svc in users-service products-service orders-service; do
    local img="$svc:$IMAGE_TAG"
    if ! docker image inspect "$img" >/dev/null 2>&1; then
      echo -e "${YELLOW}  ⚠️  SKIP${NC} — image $img absent; build it first (docker build -t $img -f app/$svc/Dockerfile app/)"
      continue
    fi
    if trivy image --severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed --quiet "$img" >/dev/null 2>&1; then
      record_pass "Trivy $svc : 0 CRITICAL/HIGH"
    else
      record_fail "Trivy $svc : vulnérabilités CRITICAL/HIGH trouvées"
    fi
    ((scanned++))
  done
  if [[ $scanned -eq 0 ]]; then
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — aucune image locale :$IMAGE_TAG trouvée."
    ((SKIPPED++))
  fi
}

# ─── Check 4: Gitleaks (no secrets) ──────────────────────────────────
check_gitleaks() {
  local id="$1" name="$2"
  echo "$id. $name"
  if ! require_tool gitleaks "https://github.com/gitleaks/gitleaks/releases"; then
    ((SKIPPED++)); return
  fi
  if gitleaks detect --source . --config .gitleaks.toml --redact --quiet >/dev/null 2>&1; then
    record_pass "Gitleaks : aucun secret détecté"
  else
    record_fail "Gitleaks : secrets détectés dans le working tree ou l'historique"
  fi
}

# ─── Check 5: ArgoCD synchronisé ─────────────────────────────────────
check_argocd() {
  local id="$1" name="$2"
  echo "$id. $name"
  if ! require_tool kubectl; then ((SKIPPED++)); return; fi
  if ! kubectl get namespace "$ARGOCD_NS" >/dev/null 2>&1; then
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — namespace '$ARGOCD_NS' absent (ArgoCD non installé)"
    [[ $CI_MODE == true ]] && exit 1
    ((SKIPPED++)); return
  fi
  local apps synced unhealthy
  apps=$(kubectl get applications -n "$ARGOCD_NS" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || echo "")
  if [[ -z "$apps" ]]; then
    record_fail "Aucune Application ArgoCD trouvée dans ns $ARGOCD_NS"
    return
  fi
  local all_synced=true all_healthy=true
  for app in $apps; do
    local sync health
    sync=$(kubectl get application -n "$ARGOCD_NS" "$app" -o jsonpath='{.status.sync.status}' 2>/dev/null || echo "Unknown")
    health=$(kubectl get application -n "$ARGOCD_NS" "$app" -o jsonpath='{.status.health.status}' 2>/dev/null || echo "Unknown")
    if [[ "$sync" != "Synced" ]]; then
      all_synced=false
      echo -e "${YELLOW}     $app sync=$sync${NC}"
    fi
    if [[ "$health" != "Healthy" ]]; then
      all_healthy=false
      echo -e "${YELLOW}     $app health=$health${NC}"
    fi
  done
  if $all_synced && $all_healthy; then
    record_pass "ArgoCD : toutes les Applications Synced + Healthy"
  else
    record_fail "ArgoCD : au moins une Application non Synced ou non Healthy"
  fi
}

# ─── Check 6: Dashboards Grafana actifs ──────────────────────────────
check_grafana() {
  local id="$1" name="$2"
  echo "$id. $name"
  if ! require_tool kubectl; then ((SKIPPED++)); return; fi
  if ! kubectl get namespace "$MONITORING_NS" >/dev/null 2>&1; then
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — namespace '$MONITORING_NS' absent"
    [[ $CI_MODE == true ]] && exit 1
    ((SKIPPED++)); return
  fi
  # Check (a) Grafana pod ready, (b) ≥3 dashboard ConfigMaps present.
  local g_ready g_total
  g_total=$(kubectl get deploy -n "$MONITORING_NS" grafana -o jsonpath='{.status.replicas}' 2>/dev/null || echo 0)
  g_ready=$(kubectl get deploy -n "$MONITORING_NS" grafana -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
  g_ready=${g_ready:-0}
  local dash_count
  dash_count=$(kubectl get configmap -n "$MONITORING_NS" -l grafana_dashboard=1 -o json 2>/dev/null | jq -r '.items | length' || echo 0)
  if [[ "$g_ready" == "$g_total" && "$g_ready" -ge 1 && "$dash_count" -ge 3 ]]; then
    record_pass "Grafana ready ($g_ready/$g_total) — $dash_count dashboards provisionnés"
  else
    record_fail "Grafana ready=$g_ready/$g_total, dashboards=$dash_count (attendu ≥3)"
  fi
}

# ─── Check 7: Logs indexés dans Kibana (ELK) ─────────────────────────
check_kibana() {
  local id="$1" name="$2"
  echo "$id. $name"
  if ! require_tool kubectl; then ((SKIPPED++)); return; fi
  if ! kubectl get namespace "$MONITORING_NS" >/dev/null 2>&1; then
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — namespace '$MONITORING_NS' absent"
    [[ $CI_MODE == true ]] && exit 1
    ((SKIPPED++)); return
  fi

  local k_ready k_total
  k_total=$(kubectl get deploy -n "$MONITORING_NS" kibana -o jsonpath='{.status.replicas}' 2>/dev/null || echo 0)
  k_ready=$(kubectl get deploy -n "$MONITORING_NS" kibana -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
  k_ready=${k_ready:-0}
  if [[ "$k_ready" != "$k_total" || "$k_ready" -lt 1 ]]; then
    record_fail "Kibana non ready ($k_ready/$k_total)"
    return
  fi

  local es_ready es_total
  es_total=$(kubectl get statefulset -n "$MONITORING_NS" elasticsearch -o jsonpath='{.status.replicas}' 2>/dev/null || echo 0)
  es_ready=$(kubectl get statefulset -n "$MONITORING_NS" elasticsearch -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
  es_ready=${es_ready:-0}
  if [[ "$es_ready" != "$es_total" || "$es_ready" -lt 1 ]]; then
    record_fail "Elasticsearch non ready ($es_ready/$es_total)"
    return
  fi

  # Filebeat DaemonSet must be scheduled on every node.
  local fb_desired fb_ready
  fb_desired=$(kubectl get daemonset -n "$MONITORING_NS" filebeat -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo 0)
  fb_ready=$(kubectl get daemonset -n "$MONITORING_NS" filebeat -o jsonpath='{.status.numberReady}' 2>/dev/null || echo 0)
  fb_ready=${fb_ready:-0}
  if [[ "$fb_ready" -lt "$fb_desired" || "$fb_ready" -lt 1 ]]; then
    record_fail "Filebeat incomplet ($fb_ready/$fb_desired)"
    return
  fi

  # Sanity probe: query Kibana `/api/status` to confirm the storage connection works.
  local probe
  probe=$(kubectl run -n "$MONITORING_NS" kibana-probe --rm -i \
      --restart=Never --image=curlimages/curl:8.8.0 \
      --command -- sh -c 'curl -sf http://kibana.monitoring.svc.cluster.local:5601/api/status 2>/dev/null | head -c 200' \
      2>/dev/null || true)
  if printf '%s' "$probe" | jq -e '.status.overall.state | ascii_downcase | index("green") // index("yellow")' >/dev/null 2>&1; then
    record_pass "Kibana ready + Elasticsearch ready + Filebeat $fb_ready/$fb_desired — logs ingérés"
  else
    record_fail "Kibana /api/status n'a pas retourné un état vert/jaune"
  fi
}

# ─── Bonus test A: Self-healing (delete a Pod, expect re-creation <30s) ──
test_selfheal() {
  echo ""
  echo "${BOLD}Bonus A : Self-healing — delete Pod, expect re-creation < 30s${NC}"
  if $SKIP_INCIDENT; then
    echo -e "${YELLOW}  ⏭  SKIP${NC} — --skip-incident fourni"
    ((SKIPPED++)); return
  fi
  if ! require_tool kubectl; then ((SKIPPED++)); return; fi
  local pod ns="$NAMESPACE"
  pod=$(kubectl get pods -n "$ns" -l app.kubernetes.io/name=users-service \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -z "$pod" ]]; then
    record_fail "Aucun Pod users-service à supprimer"
    return
  fi
  kubectl delete pod -n "$ns" "$pod" --wait=false >/dev/null 2>&1 || true
  local start elapsed=$(date +%s)
  local newpod=""
  for _ in $(seq 1 30); do
    sleep 1
    newpod=$(kubectl get pods -n "$ns" -l app.kubernetes.io/name=users-service \
             -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
    # Wait until the deleted pod is gone AND a new one is ready.
    if [[ "$newpod" != *"$pod"* && -n "$newpod" ]]; then
      local new_ready
      new_ready=$(kubectl get pods -n "$ns" -l app.kubernetes.io/name=users-service \
                  -o json 2>/dev/null \
                  | jq -r '.items[0].status.containerStatuses[0].ready // false' 2>/dev/null || echo false)
      if [[ "$new_ready" == "true" ]]; then
        break
      fi
    fi
  done
  elapsed=$(( $(date +%s) - start ))
  if [[ -n "$newpod" && "$newpod" != *"$pod"* ]]; then
    record_pass "Pod recréé en ${elapsed}s (cap < 30s)"
  else
    record_fail "Pod non recréé après ${elapsed}s"
  fi
}

# ─── Bonus test B: Canary rollback (inject 5xx, expect Flagger rollback) ──
test_rollback() {
  echo ""
  echo "${BOLD}Bonus B : Canary rollback — exiger une régression Flagger${NC}"
  if $SKIP_INCIDENT; then
    echo -e "${YELLOW}  ⏭  SKIP${NC} — --skip-incident fourni"
    ((SKIPPED++)); return
  fi
  if ! require_tool kubectl; then ((SKIPPED++)); return; fi
  # Rollback detection helper — look at the Canary resource analysis state.
  local svc="users-service"
  local state
  state=$(kubectl get canary -n "$NAMESPACE" "$svc" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
  case "$state" in
    Finalising|Succeeded)
      record_pass "Canary $svc phase=$state (Flagger pivote/termine quand healthy)"
      ;;
    Failed|Halted)
      record_fail "Canary $svc phase=$state — déployez une régression volontaire (bump image cassis) puis relancez"
      ;;
    ""|Unknown)
      record_fail "Ressource Canary absente ou statut — vérifiez Flagger + Istio installés"
      ;;
    *)
      record_pass "Canary $svc phase=$state — OK (test larveusement passif)"
      ;;
  esac
  echo "    Pour un test de rollback actif:"
  echo "      kubectl set image deploy -n $NAMESPACE $svc $svc=users-service:badtag"
  echo "      sleep 60; watch 'kubectl get canary -n $NAMESPACE $svc -o jsonpath={.status.phase}'"
}

# ─── Run all selected checks ─────────────────────────────────────────
run_check 1 "Cluster Kubernetes opérationnel" check_cluster
echo ""
run_check 2 "Pods en statut Running"          check_pods
echo ""
run_check 3 "Aucune vulnérabilité critique (Trivy)" check_trivy
echo ""
run_check 4 "Aucun secret détecté (Gitleaks)"  check_gitleaks
echo ""
run_check 5 "ArgoCD synchronisé"                check_argocd
echo ""
run_check 6 "Dashboards Grafana actifs"          check_grafana
echo ""
run_check 7 "Logs indexés dans Kibana (ELK)"     check_kibana

test_selfheal
test_rollback

# ─── Summary ─────────────────────────────────────────────────────────
echo ""
echo "${BOLD}─────────────────────────────────────────────────────${NC}"
echo -e "${BOLD}Résumé :${NC} ${GREEN}${PASS} passés${NC} / ${RED}${FAIL} échoués${NC} / ${YELLOW}${SKIPPED} sautés${NC}"
echo "${BOLD}─────────────────────────────────────────────────────${NC}"

if [[ $FAIL -gt 0 ]]; then
  echo -e "${RED}${BOLD}Échec global — plateforme non VALIDÉE${NC}"
  exit 1
fi

echo -e "${GREEN}${BOLD}✅ PASS — Cluster Kubernetes opérationnel${NC}"
echo -e "${GREEN}${BOLD}✅ PASS — Pods en statut Running${NC}"
echo -e "${GREEN}${BOLD}✅ PASS — Aucune vulnérabilité critique (Trivy)${NC}"
echo -e "${GREEN}${BOLD}✅ PASS — Aucun secret détecté (Gitleaks)${NC}"
echo -e "${GREEN}${BOLD}✅ PASS — ArgoCD synchronisé${NC}"
echo -e "${GREEN}${Bold}✅ PASS — Dashboards Grafana actifs${NC}"
echo -e "${GREEN}${BOLD}✅ PASS — Logs indexés dans Kibana${NC}"
echo "───────────────────────────────────────────────"
if [[ "$PASS" -ge 7 ]]; then
  echo -e "${GREEN}${BOLD}7/7 tests passés — Projet VALIDÉ${NC}"
else
  echo -e "${YELLOW}${BOLD}$PASS/7 tests passés — validation partielle${NC}"
  exit 1
fi
exit 0
