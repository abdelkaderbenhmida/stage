# k8s/apps

The tenant application namespace (`devops-platform`): where discovered
microservices actually run, deployed either by plain `kubectl apply`/Helm or,
under `GITOPS_ENABLED`, as ArgoCD Applications sourced from these same paths.

- `base/` — the namespace itself plus its LimitRange, ResourceQuota and
  default-deny NetworkPolicy set, applied once and independent of which (or
  how many) services are discovered.
- `chart/` — the generic Helm chart that renders per-service objects
  (Deployment, Service, ServiceAccount, HPA, PDB) plus the shared,
  render-once objects (RBAC self-read binding, ServiceMonitor). Knows no
  service names itself — the services list is injected by CI discovery or
  the ArgoCD ApplicationSet (`k8s/argocd/applicationset.yaml`).
