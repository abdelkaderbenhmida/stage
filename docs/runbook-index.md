# Incident Runbook — DevOps Central Platform

## Runbook index

| Incident | Severity | Playbook |
|---|---|---|
| Vault sealed | Critical | [runbook-vault-sealed.md](runbook-vault-sealed.md) |
| Pod crashloop | High | [runbook-pod-crashloop.md](runbook-pod-crashloop.md) |
| Prometheus down | High | [runbook-prometheus-down.md](runbook-prometheus-down.md) |
| Node NotReady | Critical | [runbook-node-notready.md](runbook-node-notready.md) |
| ImagePullBackOff | Medium | [runbook-image-pull-backoff.md](runbook-image-pull-backoff.md) |
| Full cluster loss | Disaster | [disaster-recovery.md](disaster-recovery.md) |
> **Usage**: During any incident, open the corresponding runbook and follow
> the order of commands. Each runbook has a *Symptom → Diagnosis → Fix* flow.
> After resolution, update `RAPPORT_INCIDENT_*.md` with the timeline.
