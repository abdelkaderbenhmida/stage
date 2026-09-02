# k8s/apps/chart

Generic Helm chart for the DevOps Central Platform microservices — renders one
Deployment/Service/ServiceAccount/HPA/PDB per entry in `.Values.services`.
Service discovery is external (CI directory scan, or the ArgoCD
ApplicationSet's `files` generator over `app/**/service.yaml`); this chart
hardcodes no service name.

- `Chart.yaml` — chart metadata (`devops-platform-apps`, v0.1.0).
- `values.yaml` — defaults: registry, image tag, resource sizing, HPA
  bounds, Vault address, and the two flags that control what gets rendered —
  `renderShared` (the once-only shared objects: RBAC binding, ServiceMonitor)
  and `renderSharedOnly` (render only those, none of the per-service ones).
  Per-service Applications set `renderShared: false`; the
  `devops-platform-shared` Application sets `renderSharedOnly: true`.
- `templates/deployment.yaml` — per-service Deployment: `vault-login` init
  container plus a `vault-token-refresh` sidecar authenticate against Vault's
  Kubernetes auth method and keep the token alive past its 1h TTL; the main
  container mounts the token from an in-memory `emptyDir`.
- `templates/service.yaml` — per-service Service (ClusterIP), ServiceAccount
  (`<svc>-sa`, no auto-mounted token, `ghcr-pull` image pull secret),
  HorizontalPodAutoscaler, and PodDisruptionBudget.
- `templates/rbac.yaml` — one shared `app-read-self` Role/RoleBinding
  granting every discovered service's ServiceAccount `get` on pods.
- `templates/servicemonitor.yaml` — one shared ServiceMonitor selecting every
  Service labelled `app.kubernetes.io/part-of=devops-platform`, so Prometheus
  scrapes all discovered services without per-service wiring.
- `templates/_helpers.tpl` — shared labels and the image-reference helper
  (digest-pinned when `.image` is set, otherwise `registry/name:tag`).
