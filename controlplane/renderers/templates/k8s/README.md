# renderers/templates/k8s

Kubernetes manifest templates rendered by `controlplane/renderers/namespace.py` for a
deployment's workload. These are what `runners/gitops.py` commits to the manifest repo
(GitOps path) or what a worker applies directly with `kubectl` (default path).

- `deployment.yaml.j2` — the workload Deployment.
- `service.yaml.j2` — the ClusterIP Service in front of it.
- `ingress.yaml.j2` — external routing when the deployment exposes a public host.
- `secret.yaml.j2` — per-deployment env Secret; values come from the secret store at
  render time and are never read back through the platform's own API. Never committed
  to the GitOps manifest repo, even when `GITOPS_ENABLED`.
- `rollout.yaml.j2` — Argo Rollouts `Rollout` object, used in place of a plain
  Deployment when progressive/canary delivery is configured.
- `analysis.yaml.j2` — Argo Rollouts `AnalysisTemplate` (error-rate metric) driving the
  rollout's promote/abort decision.
