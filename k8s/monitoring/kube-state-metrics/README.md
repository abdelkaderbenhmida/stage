# k8s/monitoring/kube-state-metrics

The kube-state-metrics exporter — cluster object state (as opposed to
resource usage), feeding the `kube_node_*`, `kube_pod_*`, `kube_deployment_*`
and `kube_hpa_*` series the infra dashboards and SLO/incident rules depend on.
Deployed as its own ArgoCD Application at sync-wave 16 (after Prometheus at
15, so its ServiceMonitor CRD already exists) — previously the only instance
running came from the (now-dropped) Prometheus Helm chart, so this manifest
set exists to replace that.

- `kube-state-metrics.yaml` — ServiceAccount, ClusterRole/ClusterRoleBinding
  (read-only `list`/`watch` across the object kinds it reports on),
  Deployment, Service and ServiceMonitor.
