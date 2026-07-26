# Runbook: Vault sealed

**Symptom**: Apps fail `/readyz` (503), logs show `SecretUnavailable`.

**Diagnosis**:
```bash
kubectl exec -n vault deploy/vault -- vault status
# If `sealed: true` → proceed to Unseal step.
# If `initialized: false` → bootstrap from scratch.
```

**Fix — unseal**:
```bash
kubectl exec -n vault deploy/vault -- vault operator unseal <key-1>
kubectl exec -n vault deploy/vault -- vault operator unseal <key-2>
kubectl exec -n vault deploy/vault -- vault operator unseal <key-3>
```
*(Dev mode: vault is auto-unsealed. If running standalone, distribute keys via
`scripts/bootstrap-vault-secret.sh` and store offline in a password manager.)*

**Fix — reinitialize** (only if uninitialized):
```bash
scripts/bootstrap-vault-secret.sh
kubectl delete pods -n vault -l app.kubernetes.io/name=vault
```

**Post-fix**:
```bash
kubectl rollout restart -n devops-platform deploy/users-service
scripts/validate-security.sh
```
