# k8s/monitoring/loki

Loki (log store) and promtail (log shipper) — the backend the per-project Logs
panel reads from. Deployed as one ArgoCD Application spanning two namespaces
(`loki-app.yaml`), since every object here carries an explicit
`metadata.namespace` and splitting the directory would let promtail's
`clients.url` and the Loki Service drift apart.

- `loki.yaml` — Loki's ConfigMap (single-binary, filesystem storage, no
  replication — right for a lab, not for production volume), Service (port
  3100) and Deployment (namespace `monitoring`, `Recreate` strategy since
  filesystem storage tolerates only one writer, logs live in an `emptyDir`
  so they do not survive a pod restart).
- `namespace.yaml` — the `logging` Namespace, split out so it applies before
  `promtail-config.yaml`/`promtail.yaml` sort after it alphabetically. Runs
  with the PodSecurity `privileged` profile — the exemption promtail needs
  for hostPath log mounts, scoped to this namespace alone rather than
  loosening `monitoring`.
- `promtail-config.yaml` — promtail's scrape config: discovers every pod via
  Kubernetes SD and globs `/var/log/pods/*<uid>/*.log` by pod UID, with a
  JSON pipeline stage extracting `level`/`message`/`timestamp`.
- `promtail.yaml` — promtail's RBAC (read-only nodes/services/endpoints/pods)
  and DaemonSet: runs as root (required to read root-owned host log files)
  but otherwise applies every hardening the `restricted` profile would ask
  for (no privilege escalation, all capabilities dropped, read-only root
  filesystem, seccomp). Pushes to `loki.monitoring.svc.cluster.local:3100`.
