# k8s/monitoring/elk

A second, parallel log path (alongside `../loki/`): Elasticsearch, Logstash,
Kibana and Filebeat, feeding the Elasticsearch datasource Grafana provisions
and the Kibana UI directly. Deployed as one ArgoCD Application
(`elk-app.yaml`, sync-wave 20) with `ignoreDifferences` on the Elasticsearch
StatefulSet's `volumeClaimTemplates` (a field the API server always mutates,
so git can never match it).

- `elasticsearch-values.yaml` — single-node Elasticsearch 8.14.0 StatefulSet,
  10Gi PVC, `runAsNonRoot` UID 1000, `xpack.security` enabled with a
  generated password in the (out-of-band) `elasticsearch-credentials` Secret.
- `logstash-values.yaml` — receives Beats input on 5044, forwards to
  Elasticsearch on 9200; parses the JSON stdout the FastAPI services emit so
  `level`/`service`/`event` become queryable fields in Kibana.
- `filebeat-daemonset.yaml` — tails `/var/log/containers/*.log` on every
  node with autodiscovery enrichment (namespace/container/service labels),
  runs in the `logging` namespace (not `monitoring`) for the same hostPath/
  PodSecurity reason promtail does.
- `kibana-values.yaml` — the Kibana UI, connects to Elasticsearch via the
  `kibana_system` user.
- `kibana-ingress.yaml` — Ingress exposing Kibana outside the cluster.
- `ilm-retention.yaml` — index lifecycle policy deleting old
  `tenant-<namespace>-*` / `devops-platform-*` indices; without it nothing
  bounded index growth and the volume would eventually hit Elasticsearch's
  flood-stage watermark and go read-only.
