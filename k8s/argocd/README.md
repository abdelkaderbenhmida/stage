# k8s/argocd

ArgoCD itself and the app-of-apps objects that make the GitOps deploy path
(`GITOPS_ENABLED`) self-managing from git.

- `project.yaml` — the `devops-platform` AppProject: the whitelist of source
  repos, destination namespaces (`devops-platform`, `monitoring`, `logging`,
  `gitops`, `istio-system`, `flagger-system`, `argocd`), and cluster-scoped
  kinds (CRDs, ClusterRole/ClusterRoleBinding) every Application in this tree
  is allowed to touch. This is the server-side half of tenant isolation under
  GitOps — required alongside the Application's own `destination.namespace`.
- `applicationset.yaml` — the `devops-platform-apps` ApplicationSet: one
  Application per discovered service, generated from a `files` generator over
  `app/**/service.yaml` marker files and rendered through `k8s/apps/chart`.
- `applications/` — the app-of-apps directory: one Application object per
  platform component (ArgoCD's own install, the ApplicationSet, the app base
  namespace, and every `k8s/monitoring/*` piece), each gated by a
  `argocd.argoproj.io/sync-wave` annotation for ordering.
- `install/` — the upstream ArgoCD control-plane install (kustomize remote
  base) plus the local patches this platform needs on top of it.
