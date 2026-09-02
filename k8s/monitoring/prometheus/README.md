# k8s/monitoring/prometheus

The Prometheus Operator manifest set — deployed as raw manifests, not the
Helm chart, so it stays in sync with the CR-based components elsewhere in the
platform (the apps chart's `ServiceMonitor`, `slo-rules`'
`PrometheusRule`, the `Alertmanager` CR). A `Chart.yaml` must never be added
to this directory: ArgoCD auto-detects Helm from its presence alone and would
silently render only `templates/`, ignoring every manifest here.

- `crds.yaml` — the prometheus-operator v0.74.0 CRDs (Prometheus,
  Alertmanager, ServiceMonitor, PrometheusRule, etc). Must apply before
  everything else in this directory.
- `operator.yaml` — the operator Deployment, ServiceAccount and ClusterRole,
  with `--kubelet-service` enabled so it maintains the `kubelet` Service in
  `kube-system` that `../kubelet/kubelet-scrape.yaml` scrapes.
- `prometheus.yaml` — the `Prometheus` CR itself: 15d retention, 8GiB PVC
  cap, `serviceMonitorSelector: {}` / `ruleSelector: {}` (picks up every
  ServiceMonitor and PrometheusRule cluster-wide — a single shared TSDB with
  no per-tenant query isolation, noted as acceptable only because nothing
  tenant-facing queries it directly).
- `rbac.yaml` — RBAC for the Prometheus pod itself (scrape access to nodes,
  services, endpoints, pods, `/metrics`, `/metrics/cadvisor`).
- `service.yaml` — ClusterIP Service on port 9090, DNS name referenced by the
  Grafana datasource.
