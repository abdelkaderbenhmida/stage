# k8s/apps/base

The `devops-platform` namespace's fixed scaffolding — applied once, and not
re-rendered per discovered service. Bundled as a Kustomization so it can be
applied (or synced by ArgoCD, see `k8s/argocd/applications/base-app.yaml`) as
one unit.

- `kustomization.yaml` — lists the two resources below; nothing else to
  configure here.
- `namespace.yaml` — the `devops-platform` Namespace (with the Pod Security
  Admission `restricted` labels enforced), a `LimitRange` giving unannotated
  containers sane request/limit defaults, and a `ResourceQuota` capping
  aggregate CPU/memory/object counts for the whole namespace.
- `networkpolicies.yaml` — default-deny ingress+egress, then explicit
  allow rules: apps → Vault (8200), apps → DNS, apps → postgres, Prometheus
  → app `/metrics` scrape, and intra-namespace service-to-service traffic.
