# k8s/monitoring/kubelet

A single ServiceMonitor scraping each node's kubelet.

- `kubelet-scrape.yaml` — scrapes `/metrics/cadvisor` (feeds the per-project
  CPU/memory panels in `controlplane/api/routers/monitoring.py`) and
  `/metrics` (node-level views) on every node, via `honorLabels: true` so
  scraped series keep the workload's own pod/namespace labels rather than
  being attributed to `kube-system/kubelet`. Targets the Service/Endpoints
  the Prometheus Operator itself maintains (`--kubelet-service`, see
  `../prometheus/operator.yaml`) rather than hardcoded node IPs — the
  previous version pinned VM-mode addresses and silently scraped nothing on
  any other cluster shape.
