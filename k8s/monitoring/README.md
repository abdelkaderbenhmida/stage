# k8s/monitoring

The platform's own observability stack: metrics (Prometheus Operator +
exporters), alerting (Alertmanager + SLO rules), dashboards (Grafana), and two
parallel log paths (Loki/promtail and the ELK stack). Deployed either by
`kubectl apply` per subdirectory or, under `GITOPS_ENABLED`, as one ArgoCD
Application per subdirectory (`k8s/argocd/applications/`), ordered by
sync-wave since the Prometheus Operator CRDs must land before anything that
defines a `ServiceMonitor`/`PrometheusRule`/`Alertmanager` CR.

- `base/` — the `monitoring` namespace, LimitRange and ResourceQuota.
- `prometheus/` — the Prometheus Operator (CRDs, controller, RBAC) and the
  `Prometheus` instance itself.
- `alertmanager/` — the `Alertmanager` instance, its routing config, SLO/
  incident `PrometheusRule`s, and an in-cluster webhook sink for alerts.
- `grafana/` — Grafana deployment, datasources, and dashboard provisioning.
- `grafana/dashboards/` — the four dashboard ConfigMaps Grafana's sidecar
  loads.
- `loki/` — Loki (log store) and promtail (log shipper, runs in its own
  `logging` namespace).
- `elk/` — Elasticsearch, Logstash, Kibana and Filebeat — a second, parallel
  log path used for the tenant-facing log views.
- `kubelet/` — a ServiceMonitor scraping every node's kubelet/cAdvisor.
- `kube-state-metrics/` — the kube-state-metrics exporter (cluster object
  state: pods, deployments, HPAs, etc).
