# k8s/monitoring/base

Fixed scaffolding for the `monitoring` namespace, applied once ahead of any
component in it (ArgoCD sync-wave 5, see
`k8s/argocd/applications/observability-base-app.yaml`).

- `namespace.yaml` — the `monitoring` Namespace with Pod Security Admission
  `restricted` labels enforced, a `LimitRange` (500m/512Mi container
  defaults), and a `ResourceQuota` sized for the whole observability stack
  (6 CPU / 8Gi requests, 30 pods).
