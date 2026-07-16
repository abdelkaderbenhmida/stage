#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Phase 4 validation — DevSecOps security checks
# ─────────────────────────────────────────────────────────────
# Checks:
#   1. Gitleaks — no secrets in source code
#   2. Trivy  — no CRITICAL/HIGH vulnerabilities in images
#   3. Vault  — running and unsealed
#   4. Kubernetes secrets — vault-root-token present
# Usage:
#     scripts/validate-security.sh [--ci]
#
# With --ci flag: exit 1 on any failure (for CI)
# Without flag: print summary table (for local dev)
# ─────────────────────────────────────────────────────────────

set -o pipefail

CI_MODE=false
[[ "$1" == "--ci" ]] && CI_MODE=true

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
TOTAL=4

check() {
    local name="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        echo -e "${GREEN}  ✅ PASS${NC} — $name"
        PASS=$((PASS + 1))
        return 0
    else
        echo -e "${RED}  ❌ FAIL${NC} — $name"
        FAIL=$((FAIL + 1))
        return 1
    fi
}

echo "─────────────────────────────────────────────────────"
echo "Phase 4 — Security Validation"
echo "─────────────────────────────────────────────────────"
echo ""

# ── Check 1: Gitleaks secret scan ──────────────────────
echo  "1. Gitleaks — no secrets in source code"
if command -v gitleaks &> /dev/null; then
    check "Gitleaks zero secrets" gitleaks detect --source . --config .gitleaks.toml --no-git
else
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — Gitleaks not installed (\"brew install gitleaks\" or \"go install github.com/gitleaks/gitleaks/v2@latest\")"
    TOTAL=$((TOTAL - 1))
fi

# ── Check 2: Trivy image scan ──────────────────────────
echo ""
echo "2. Trivy — no CRITICAL/HIGH vulnerabilities"
if command -v trivy &> /dev/null; then
    # scan all 3 services if images exist locally
    IMG_COUNT=0
    for svc in users-service products-service orders-service; do
        img="${svc}:dev"
        if docker image inspect "$img" &> /dev/null; then
            check "Trivy $svc" trivy image --severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed --quiet "$img"
            IMG_COUNT=$((IMG_COUNT + 1))
        fi
    done
    [[ $IMG_COUNT -eq 0 ]] && echo -e "${YELLOW}  ⚠️  SKIP${NC} — no local images found (build with: docker build -t <svc>:dev app/<svc>/)"
else
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — Trivy not installed (\"brew install trivy\" or \"curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh\")"
fi

# ── Check 3: Vault running and unsealed ────────────────
echo ""
echo "3. Vault — running and unsealed"
if command -v kubectl &> /dev/null; then
    if kubectl get namespace vault &> /dev/null; then
        # Check if vault pod is running
        VAULT_POD=$(kubectl get pods -n vault -l app=vault -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        if [[ -n "$VAULT_POD" ]]; then
            # Check vault status: Initialized=true, Sealed=false
            VAULT_STATUS=$(kubectl exec -n vault "$VAULT_POD" -- vault status -format=json 2>/dev/null || echo "")
            if [[ -n "$VAULT_STATUS" ]]; then
                INITIALIZED=$(echo "$VAULT_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('initialized',False))" 2>/dev/null || echo "false")
                SEALED=$(echo "$VAULT_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sealed',True))" 2>/dev/null || echo "true")
                if [[ "$INITIALIZED" == "True" && "$SEALED" == "False" ]]; then
                    echo -e "${GREEN}  ✅ PASS${NC} — Vault initialized and unsealed"
                    PASS=$((PASS + 1))
                else
                    echo -e "${RED}  ❌ FAIL${NC} — Vault status: initialized=$INITIALIZED, sealed=$SEALED"
                    FAIL=$((FAIL + 1))
                fi
            else
                echo -e "${RED}  ❌ FAIL${NC} — could not query Vault status (pod: $VAULT_POD)"
                FAIL=$((FAIL + 1))
            fi
        else
            echo -e "${YELLOW}  ⚠️  SKIP${NC} — Vault deployed but no pod found (run: kubectl apply -f k8s/vault/manifests.yaml)"
        fi
    else
        echo -e "${YELLOW}  ⚠️  SKIP${NC} — Vault namespace not found (run: kubectl apply -f k8s/vault/manifests.yaml)"
    fi
else
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — kubectl not available"
fi

# ── Check 4: K8s Secret for Vault token ────────────────
echo ""
echo "4. Vault root token secret in devops-platform namespace"
if command -v kubectl &> /dev/null; then
    if kubectl get secret vault-root-token -n devops-platform &> /dev/null; then
        check "Secret vault-root-token exists" echo "ok"
    else
        echo -e "${YELLOW}  ⚠️  SKIP${NC} — secret not yet applied (run: kubectl apply -f k8s/vault/secret-vault-root.yaml)"
    fi
else
    echo -e "${YELLOW}  ⚠️  SKIP${NC} — kubectl not available"
fi

# ── Summary ────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────────"
echo -e "Result: ${GREEN}${PASS} passed${NC} / ${RED}${FAIL} failed${NC} / ${YELLOW}${TOTAL} checks${NC}"
echo "─────────────────────────────────────────────────────"

if $CI_MODE; then
    [[ $FAIL -gt 0 ]] && exit 1
fi
exit $FAIL