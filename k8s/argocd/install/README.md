# k8s/argocd/install

The upstream ArgoCD control-plane install, pulled via a kustomize remote base
and patched locally. Synced by `argocd-install-app.yaml` at sync-wave -100 so
it lands before any other Application.

- `kustomization.yaml` — pins `cluster-install` (not `core-install`, which
  drops `argocd-server`/dex) to `argo-cd v3.5.1`, applies the two patches
  below, and sets `namespace: argocd`. The version pin tracks the cluster's
  own Kubernetes version, not preference — see the file's comment on
  `.status.terminatingReplicas` for why a stale pin breaks every diff.
- `anonymous-access-patch.yaml` — enables anonymous read-only (`role:
  readonly`) access and serves the UI over plain HTTP with framing headers
  disabled, so the platform console can embed the ArgoCD web console in an
  iframe without a login.
- `timeouts-patch.yaml` — raises the repo-server/exec/ApplicationSet git
  timeouts from their defaults (60-90s) to 300s, needed on a slow or jittery
  link to this repo.
