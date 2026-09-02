# k8s/gitops

The platform's manifest repository — an in-cluster Gitea instance that ArgoCD
reads tenant workload manifests from under `GITOPS_ENABLED`. Only YAML the
control plane itself rendered lives in the repo it hosts (one directory per
`<project-namespace>/<service>`); tenant source code and rendered Secrets
never reach it (see `runners/gitops.py`).

- `git-server.yaml` — the `gitops` Namespace, a 5Gi PVC, a NodePort Service
  (30300, reachable from the worker's sandbox container on the host's docker
  network — see `GITOPS_REPO_URL` vs `GITOPS_REPO_URL_INTERNAL` in the root
  `CLAUDE.md`), the Gitea Deployment itself (an init container bootstraps the
  first admin account and writes `app.ini`), and a post-sync Job that creates
  the `tenants` repository via the Gitea API. The `git-server-admin` /
  `git-server-repo` credential Secrets are deliberately not declared here —
  see the file's header comment for why and how to create them out-of-band.
