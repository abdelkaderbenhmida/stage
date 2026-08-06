#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# DevSecOps security validation — Phase 4+ post-remediation checks.
# ─────────────────────────────────────────────────────────────────────────────
# Fixes from devops-analysis-report.md (P0 #6, P2):
#   - `set -euo nounset` so unset vars and command failures stop execution.
#   - FAIL (not SKIP) on missing tools when --ci is set — no silent green.
#   - Use `jq` for Vault status JSON parsing (no fragile python3 dependency).
#   - Scan :latest (matches actual build tag) not :dev — see CI build job.
#   - Check 4 actually validates the token (logs into Vault) — no `echo ok`.
#   - Use `gitleaks detect` with full git history (no --no-git).
#
# Usage:
#   scripts/validate-security.sh             # local: print colored summary
#   scripts/validate-security.sh --ci        # CI: exit 1 on any failure
#
# Exit codes:
#   0 — all checks passed
#   1 — at least one check failed
#   2 — usage / configuration error
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

CI_MODE=false
IMAGE_TAG="latest"
NAMESPACE="devops-platform"
VAULT_NS="vault"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ci) CI_MODE=true ; shift ;;
    --tag) IMAGE_TAG="${2:?--tag requires a value}" ; shift 2 ;;
    --namespace) NAMESPACE="${2:?--namespace requires a value}" ; shift 2 ;;
    -h|--help)
      sed -n '2,28p' "$0"
      exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2 ; exit 2 ;;
  esac
done

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
NC=$'\033[0m'

PASS=0
FAIL=0
SKIPPED=0

# A check is "FAIL" (not "SKIP") when a tool is missing in --ci mode — that is
# a CI misconfiguration, not a green status. In local mode we SKIP gracefully.
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

record_pass() { local name="$1"; echo -e "${GREEN}  ✅ PASS${NC} — $name" ; PASS=$((PASS+1)) ; }
record_fail() { local name="$1"; echo -e "${RED}  ❌ FAIL${NC} — $name" >&2 ; FAIL=$((FAIL+1)) ; }

run_check() {
  # run_check "<name>" <command...>
  local name="$1" ; shift
  if "$@" >/dev/null 2>&1; then
    record_pass "$name"
  else
    record_fail "$name"
  fi
}

echo "─────────────────────────────────────────────────────"
echo " Security Validation (mode: $([[ $CI_MODE == true ]] && echo CI || echo local), tag: :$IMAGE_TAG)"
echo "─────────────────────────────────────────────────────"
echo ""

# ── Check 1: Gitleaks secret scan with full history ─────────────────────
echo "1. Gitleaks — no secrets in working tree or history"
if require_tool gitleaks "https://github.com/gitleaks/gitleaks/releases" ; then
  run_check "Gitleaks zero secrets" \
    gitleaks detect --source . --config .gitleaks.toml --redact --no-banner
fi

# ── Check 2: Trivy image scan ──────────────────────────
echo ""
echo "2. Trivy — no CRITICAL/HIGH vulnerabilities in :$IMAGE_TAG images"
if require_tool trivy "https://aquasecurity.github.io/trivy/latest/install/" ; then
  if command -v docker >/dev/null 2>&1; then
    scanned=0
    for svc in users-service products-service orders-service; do
      img="${svc}:${IMAGE_TAG}"
      if docker image inspect "$img" >/dev/null 2>&1; then
        run_check "Trivy $svc" trivy image \
          --severity CRITICAL,HIGH \
          --exit-code 1 \
          --ignore-unfixed \
          --quiet \
          "$img"
        scanned=$((scanned+1))
      fi
    done
    if [[ $scanned -eq 0 ]]; then
      echo -e "${YELLOW}  ⚠️  SKIP${NC} — no local images with :$IMAGE_TAG found (build with: docker build -t <svc>:$IMAGE_TAG -f app/<svc>/Dockerfile app/)"
      SKIPPED=$((SKIPPED+1))
    fi
  else
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — docker not installed; cannot inspect images." >&2
    [[ $CI_MODE == true ]] && exit 1
    SKIPPED=$((SKIPPED+1))
  fi
fi

# ── Check 3: Vault running, unsealed, and reachable ──
echo ""
echo "3. Vault — running, unsealed, and API reachable"
if require_tool kubectl "https://kubernetes.io/docs/tasks/tools/install-kubectl/" ; then
  if kubectl get namespace "$VAULT_NS" >/dev/null 2>&1; then
    VAULT_POD=$(kubectl get pods -n "$VAULT_NS" -l app.kubernetes.io/name=vault \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    if [[ -n "$VAULT_POD" ]]; then
      STATUS_JSON=$(kubectl exec -n "$VAULT_NS" "$VAULT_POD" -- \
        vault status -format=json 2>/dev/null || true)
      if [[ -n "$STATUS_JSON" ]]; then
        # Parse with jq (no python3 dependency).
        INITIALIZED=$(printf '%s' "$STATUS_JSON" | jq -r '.initialized' 2>/dev/null || echo "false")
        SEALED=$(printf '%s' "$STATUS_JSON" | jq -r '.sealed' 2>/dev/null || echo "true")
        if [[ "$INITIALIZED" == "true" && "$SEALED" == "false" ]]; then
          record_pass "Vault initialized and unsealed (sealed=$SEALED)"
        else
          record_fail "Vault bad state: initialized=$INITIALIZED sealed=$SEALED"
        fi
      else
        record_fail "could not query vault status (pod: $VAULT_POD)"
      fi
    else
      record_fail "Vault deployed but no pod found (apply k8s/vault/manifests.yaml)"
    fi
  else
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — namespace '$VAULT_NS' not found." >&2
    [[ $CI_MODE == true ]] && exit 1
    SKIPPED=$((SKIPPED+1))
  fi
fi

# ── Check 4: vault-root-token Secret actually authenticates to Vault ──
# The old check just ran `echo "ok"` — a no-op that always passed. Replace
# with a real probe: read the actual secret value, log into Vault, query
# sys/health → if Vault accepts the token, the secret is alive. If rejected
# (403 / invalid), the secret has drifted or never been bootstrapped.
echo ""
echo "4. vault-root-token Secret — present AND a live Vault token"
if command -v kubectl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
  if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    if kubectl get secret vault-root-token -n "$NAMESPACE" >/dev/null 2>&1; then
      TOKEN=$(kubectl get secret vault-root-token -n "$NAMESPACE" \
        -o jsonpath='{.data.root-token}' 2>/dev/null | base64 -d 2>/dev/null || true)
      if [[ -z "$TOKEN" ]]; then
        record_fail "Secret exists but root-token key is empty"
      elif [[ "$TOKEN" == *"INJECT-VIA"* || "$TOKEN" == *"DO-NOT-COMMIT"* ]]; then
        record_fail "Secret still contains the placeholder value — bootstrap with scripts/bootstrap-vault-secret.sh"
      else
        # Talk to Vault via exec into the running vault pod (avoids probe pod
        # networkpolicy/PSA friction).
        body=$(kubectl exec -n "$VAULT_NS" "$VAULT_POD" -- \
          env VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN="$TOKEN" \
          vault token lookup -format=json 2>/dev/null || true)
        if printf '%s' "$body" | jq -e '.data.id' >/dev/null 2>&1; then
          lifetime=$(printf '%s' "$body" | jq -r '.data.ttl // "unknown"' 2>/dev/null || echo "unknown")
          record_pass "vault-root-token authenticates to Vault (ttl=${lifetime}s)"
        else
          record_fail "vault-root-token rejected by Vault (token invalid / expired / unauthenticated)"
        fi
      fi
    else
      record_fail "Secret 'vault-root-token' missing in '$NAMESPACE' (bootstrap: scripts/bootstrap-vault-secret.sh)"
    fi
  else
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — namespace '$NAMESPACE' not found." >&2
    [[ $CI_MODE == true ]] && exit 1
    SKIPPED=$((SKIPPED+1))
  fi
else
  echo -e "${YELLOW}  ⚠️  SKIP${NC} — kubectl or jq missing." >&2
  [[ $CI_MODE == true ]] && exit 1
  SKIPPED=$((SKIPPED+1))
fi

# ── Summary ────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────────"
echo -e "Result: ${GREEN}${PASS} passed${NC} / ${RED}${FAIL} failed${NC} / ${YELLOW}${SKIPPED} skipped${NC}"
echo "─────────────────────────────────────────────────────"

# Any failure → exit 1 (the new contract: never `exit $FAIL` since 0..4 wraps weirdly).
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
exit 0
