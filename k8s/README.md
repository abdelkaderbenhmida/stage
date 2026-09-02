# k8s

Kubernetes manifests for the platform's own infrastructure — everything the
control plane provisions or expects to find in a cluster, as distinct from
tenant application code. Two install paths coexist: plain `kubectl apply` /
Helm (the default), and an ArgoCD GitOps path that is opt-in via
`GITOPS_ENABLED` (see the repo root `CLAUDE.md`). `TEKTON_ENABLED` adds a
third, in-cluster CI path for tenant builds.

- `apps/` — the tenant workload namespace: base namespace/quota/network
  policy plus the generic Helm chart (`apps/chart`) that renders one
  Deployment/Service/HPA/PDB per discovered service.
- `argocd/` — the ArgoCD control plane itself (`install/`) and the
  app-of-apps Applications/ApplicationSet that drive the GitOps path
  (`applications/`, `applicationset.yaml`, `project.yaml`).
- `gitops/` — the in-cluster git server (Gitea) that ArgoCD syncs rendered
  tenant manifests from.
- `monitoring/` — Prometheus Operator, Alertmanager, Grafana, Loki/promtail,
  the ELK stack, and supporting exporters (kubelet, kube-state-metrics).
- `policies/` — Kyverno cluster policies and conftest/Rego rules that gate
  workload manifests.
- `tekton/` — the tenant build pipeline and per-namespace dashboard used
  when `TEKTON_ENABLED=true`.
- `vault/` — HashiCorp Vault deployment and per-service secret bootstrap.

No files live directly in this directory; every manifest sits under one of
the subdirectories above, each with its own README.
