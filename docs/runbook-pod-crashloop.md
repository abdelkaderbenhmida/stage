# Runbook: Pod crashloop

**Symptom**: `kubectl get pods -n devops-platform` shows CrashLoopBackOff.

**Diagnosis**:
```bash
# Check logs — typically SecretUnavailable / DB connection error.
kubectl logs -n devops-platform deploy/users-service --tail 50

# Describe for events (OOM, probe failures).
kubectl describe pod -n devops-platform -l app.kubernetes.io/name=users-service

# Check Vault health from inside the pod.
kubectl exec -n devops-platform deploy/users-service -- \
  curl -sf http://127.0.0.1:8000/readyz
```

**Fix**:
1. **SecretUnavailable**: Vault sealed or token expired → [Vault sealed runbook](runbook-vault-sealed.md)
2. **OOMKilled**: Increase memory limits in `k8s/apps/*-deployment.yaml`
3. **Probe failure**: Check `/livez` vs `/readyz` — if Vault is healthy but read
z returns 503, the probe may need a longer timeout.
4. **ImagePullBackOff**: [Image pull backoff runbook](runbook-image-pull-backoff.md)

**Post-fix**:
```bash
kubectl rollout status -n devops-platform deploy/users-service --timeout=120s
```
