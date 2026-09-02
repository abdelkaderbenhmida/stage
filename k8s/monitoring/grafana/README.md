# k8s/monitoring/grafana

Grafana, deployed from raw manifests (not the `grafana/grafana` Helm chart —
see the header comment in `deployment.yaml` and
`k8s/argocd/applications/grafana-app.yaml` for the selector-immutability
conflict that ruled the chart out). Datasources and dashboards are both
provisioned automatically at startup, no UI clicks required.

- `deployment.yaml` — the Grafana Deployment plus a `kiwigrid/k8s-sidecar`
  container that watches ConfigMaps labelled `grafana_dashboard=1`
  cluster-wide and writes their JSON into an `emptyDir` Grafana reads from.
  Anonymous read-only (Viewer) access and embedding are both enabled so the
  operator console can iframe it; admin login is still required for
  edit/alerting/admin.
- `rbac.yaml` — a separate ServiceAccount/ClusterRole for the sidecar
  (`grafana-sc-dashboard`), since it alone needs cluster-wide ConfigMap
  list/watch to find dashboards regardless of namespace.
- `configmap-datasources.yaml` — provisions Prometheus (default) and an
  Elasticsearch datasource pointed at the ELK stack.
- `configmap-dashboards-provider.yaml` — tells Grafana to load `*.json` from
  the sidecar's `emptyDir` every 30s.
- `secret.yaml` — declares no Secret on purpose; documents how to create
  `grafana-admin-credentials` out-of-band so GitOps `selfHeal` never reverts
  a rotated password to a committed placeholder.
- `service.yaml` — ClusterIP Service on port 3000.
