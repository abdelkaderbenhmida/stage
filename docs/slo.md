# SLO / SLI documentation — DevOps Central Platform

## Service Level Objectives

| Service | SLO | SLI | Measurement |
|---|---|---|---|
| Users API | 99.9% availability (30d) | `/livez` success rate | `avg_over_time(probe_success[30d])` |
| Products API | 99.9% availability (30d) | `/livez` success rate | `avg_over_time(probe_success[30d])` |
| Orders API | 99.5% availability (30d) | `/livez` success rate | `avg_over_time(probe_success[30d])` |
| Vault | 99.99% availability (7d) | `/v1/sys/health` | `avg_over_time(vault_core_unsealed[7d])` |

## Error budget

SLO 99.9% → 43 min/month downtime allowed.
SLO 99.5% → 3.65 h/month.

## Prometheus recording rules

```yaml
groups:
  - name: slo-rules
    interval: 5m
    rules:
      - record: slo:error_budget_remaining:ratio
        expr: |
          1 - (
            sum(rate(http_requests_duration_seconds_count{status=~"5.."}[30d]))
            /
            sum(rate(http_requests_duration_seconds_count[30d]))
          )
        labels:
          slo: "99.9"

      - record: slo:latency_p99:seconds
        expr: histogram_quantile(0.99, sum(rate(http_requests_duration_seconds_bucket[5m])) by (le, service))
```

## Alerting thresholds

| Alert | Condition | Severity |
|---|---|---|
| ErrorBudgetBurn | `slo:error_budget_remaining:ratio < 0.9` | P1 |
| HighLatencyP99 | `slo:latency_p99:seconds > 1.0` | P2 |
| VaultDown | `vault_core_unsealed == 0` | Critical |
