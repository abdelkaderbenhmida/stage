# k8s/argocd/applications

The app-of-apps directory: one ArgoCD `Application` per platform component,
applied as a unit by `applications-app.yaml` itself (via
`devops-platform-applicationset` → `k8s/argocd`, since a plain directory
source is not recursive). Ordering between them is `argocd.argoproj.io/
sync-wave` — lower numbers land first.

- `argocd-install-app.yaml` — wave -100, ArgoCD's own self-bootstrap from
  `k8s/argocd/install`.
- `applicationset-app.yaml` — wave -50, syncs `k8s/argocd` (the
  ApplicationSet + AppProject) so per-service Applications get generated.
- `applications-app.yaml` — wave 0, this directory itself; `prune: false`
  until it is known to be the complete set of hand-created Applications.
- `base-app.yaml` — the `devops-platform` namespace scaffolding
  (`k8s/apps/base`).
- `shared-app.yaml` — the once-only shared chart objects (RBAC binding,
  ServiceMonitor) from `k8s/apps/chart`, with the full service list supplied
  by name so the RoleBinding subjects stay complete.
- `gitops-app.yaml` — wave 5, the in-cluster git server (`k8s/gitops`) that
  every tenant Application syncs from.
- `observability-base-app.yaml` — wave 5, the `monitoring` namespace
  scaffolding (`k8s/monitoring/base`).
- `prometheus-app.yaml` — wave 15, the Prometheus Operator manifest set
  (deliberately not the Helm chart — see the file's comment on why a
  `Chart.yaml` here would break every CR-based component).
- `kube-state-metrics-app.yaml` — wave 16, cluster object metrics exporter.
- `grafana-app.yaml` / `grafana-dashboards-app.yaml` — wave 25 / default,
  Grafana itself and its four dashboard ConfigMaps.
- `alertmanager-app.yaml` / `slo-rules-app.yaml` — wave 20, both sourced from
  `k8s/monitoring/alertmanager` but split by directory `include`/`exclude` on
  `rules.yaml` so Alertmanager and the SLO PrometheusRule sync independently.
- `elk-app.yaml` — wave 20, the ELK stack; ignores
  `spec/volumeClaimTemplates` drift on the Elasticsearch StatefulSet (a field
  the API server always mutates and git can never match).
- `loki-app.yaml` — wave 20, Loki (in `monitoring`) and promtail (in
  `logging`) applied together as one unit.
