# k8s/monitoring/alertmanager

The `Alertmanager` instance, its routing configuration, the SLO/incident
alerting rules, and a minimal in-cluster receiver so alerts have somewhere to
land without an external SaaS account. Synced as two separate ArgoCD
Applications from this one directory (`alertmanager-app.yaml` excludes
`rules.yaml`, `slo-rules-app.yaml` includes only it), so Alertmanager and the
SLO rules can sync independently.

- `alertmanager.yaml` — the `Alertmanager` CR: 1 replica, 2Gi PVC, wired to
  the `AlertmanagerConfig` below via `alertmanagerConfiguration.name` (chosen
  over a selector, which would inject a namespace matcher into every route).
- `alertmanager-config.yaml` — routing: groups by `alertname`+`service`,
  batches every 5m, default receiver posts to the in-cluster `alert-sink`
  webhook.
- `alert-sink.yaml` — a stdlib-only Python HTTP server (ConfigMap-mounted
  code, no custom image) that buffers the last 500 alert deliveries in memory
  and serves them at `GET /alerts` for the platform UI. Memory-only — history
  is lost on pod restart.
- `rules.yaml` — the `slo-rules` `PrometheusRule`: availability < 99.9%
  over 30d, P95 latency > 200ms, 5xx rate > 1%, plus two incident-response
  rules (container memory ratio > 80% / OOMKilled, and HPA pinned at
  `maxReplicas` for >15m).
- `service.yaml` — ClusterIP Service on port 9093, referenced by the
  Prometheus instance's `alerting.alertmanagers` config.
