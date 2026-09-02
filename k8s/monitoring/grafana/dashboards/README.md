# k8s/monitoring/grafana/dashboards

Dashboard JSON, each wrapped in a ConfigMap labelled `grafana_dashboard: "1"`
so Grafana's sidecar (`../deployment.yaml`) picks it up automatically. Synced
as its own ArgoCD Application (`grafana-dashboards-app.yaml`), separate from
Grafana itself.

- `01-infra-overview.yaml` — cluster-wide rollup: nodes, CPU, memory, pod
  counts. Sourced from cAdvisor (`container_*`) and kube-state-metrics
  (`kube_node_*`, `kube_pod_info`).
- `02-app-performance.yaml` — per-endpoint RPS and P95/P99 latency, from
  `prometheus-fastapi-instrumentator` metrics.
- `03-error-rate.yaml` — 5xx/4xx rate per service against the 1% SLO
  threshold from `../../alertmanager/rules.yaml`.
- `04-infra-detail.yaml` — per-node drill-down and saturation signals tied to
  the memory-ratio and HPA-runaway incident rules (also in
  `../../alertmanager/rules.yaml`); requires cAdvisor and kube-state-metrics.
