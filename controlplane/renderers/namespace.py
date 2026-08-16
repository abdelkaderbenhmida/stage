"""Namespace-mode rendering (docs/TODO.md Task 2.3).

Instead of provisioning VMs, this carves a bounded slice out of a shared
cluster: a namespace with a ResourceQuota, a LimitRange, a default-deny
NetworkPolicy and a scoped ServiceAccount.

The quota is what stops one tenant starving every other tenant on the shared
cluster, and the NetworkPolicy is what stops one tenant reaching another's
pods. Neither is optional — a namespace without them is not isolation, it is
just a naming convention.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from controlplane.schemas.spec import InfraSpec

# Namespace mode has no VMs, so the spec's per-node sizing is reinterpreted as
# the total budget for the namespace.
_CPU_OVERCOMMIT = 2  # requests may total half of the limit ceiling


def _totals(spec: InfraSpec) -> tuple[int, int, int]:
    vcpu = sum(node.vcpu for node in spec.nodes)
    memory_mb = sum(node.memory_mb for node in spec.nodes)
    disk_gb = sum(node.disk_gb for node in spec.nodes)
    return vcpu, memory_mb, disk_gb


def build_manifests(spec: InfraSpec, namespace: str) -> list[dict]:
    """Return the manifest documents defining an isolated tenant namespace.

    ``namespace`` is the collision-proof cluster identity (see
    ``core.validation.k8s_namespace``) — never ``spec.project``, which is
    only the human-chosen display name and is unique per *team*, not
    globally: two teams can each name a project "staging".
    """
    vcpu, memory_mb, disk_gb = _totals(spec)

    labels = {
        "app.kubernetes.io/managed-by": "devops-central-platform",
        "platform.devops/project": spec.project,
        "platform.devops/mode": "namespace",
    }

    manifests = [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace, "labels": labels},
        },
        {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {"name": f"{namespace}-quota", "namespace": namespace},
            "spec": {
                "hard": {
                    "requests.cpu": str(vcpu // _CPU_OVERCOMMIT or 1),
                    "requests.memory": f"{memory_mb // _CPU_OVERCOMMIT}Mi",
                    "limits.cpu": str(vcpu),
                    "limits.memory": f"{memory_mb}Mi",
                    "requests.storage": f"{disk_gb}Gi",
                    "persistentvolumeclaims": "8",
                    "pods": "40",
                    "services": "20",
                    # Node ports and load balancers are cluster-wide scarce
                    # resources; tenants get neither.
                    "services.nodeports": "0",
                    "services.loadbalancers": "0",
                }
            },
        },
        {
            "apiVersion": "v1",
            "kind": "LimitRange",
            "metadata": {"name": f"{namespace}-limits", "namespace": namespace},
            "spec": {
                "limits": [
                    {
                        "type": "Container",
                        # A container with no explicit limits would otherwise
                        # be unbounded and could exhaust the quota alone.
                        "default": {"cpu": "500m", "memory": "512Mi"},
                        "defaultRequest": {"cpu": "100m", "memory": "128Mi"},
                        "max": {"cpu": str(vcpu), "memory": f"{memory_mb}Mi"},
                    }
                ]
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": f"{namespace}-default-deny", "namespace": namespace},
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    # Same-namespace traffic and the ingress controller only.
                    {"from": [{"podSelector": {}}]},
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}
                                }
                            }
                        ]
                    },
                ],
                "egress": [
                    {"to": [{"podSelector": {}}]},
                    # DNS must stay reachable or nothing in the namespace
                    # resolves anything.
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                                }
                            }
                        ],
                        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
                    },
                    # Outbound internet, minus link-local: 169.254.169.254 is
                    # the cloud metadata endpoint and would hand any pod the
                    # node's credentials.
                    {
                        "to": [
                            {
                                "ipBlock": {
                                    "cidr": "0.0.0.0/0",
                                    "except": [
                                        "169.254.0.0/16",
                                        "10.0.0.0/8",
                                        "172.16.0.0/12",
                                        "192.168.0.0/16",
                                    ],
                                }
                            }
                        ]
                    },
                ],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": f"{namespace}-sa", "namespace": namespace, "labels": labels},
            # Workloads here do not talk to the Kubernetes API; mounting a
            # token only widens the blast radius of a compromised pod.
            "automountServiceAccountToken": False,
        },
        # Observability tier (§4.1/4.2): every environment is scrapeable by
        # the central Prometheus via its own ServiceMonitor. The central
        # Promtail already ships pod logs for every namespace on the shared
        # cluster, so the default "light" tier needs nothing else. Full-tier
        # ServiceMonitors carry a tier label so SLO alerting (§4.3) can fire
        # only for environments that opted into pages.
        {
            "apiVersion": "monitoring.coreos.com/v1",
            "kind": "ServiceMonitor",
            "metadata": {
                "name": f"{namespace}-monitor",
                "namespace": "monitoring",
                "labels": {
                    "app.kubernetes.io/name": f"{namespace}-monitor",
                    "app.kubernetes.io/part-of": "devops-platform",
                    "platform.devops/project": namespace,
                    **({"platform.devops/tier": "full"} if spec.observability == "full" else {}),
                },
            },
            "spec": {
                "jobLabel": "app.kubernetes.io/name",
                "selector": {
                    "matchLabels": {"platform.devops/project": namespace}
                },
                "namespaceSelector": {"matchNames": [namespace]},
                "endpoints": [
                    {
                        "port": "http",
                        "path": "/metrics",
                        "interval": "30s",
                    }
                ],
            },
        },
    ]
    if spec.observability == "full":
        manifests.extend(_full_tier_manifests(namespace, labels))
    return manifests


def _full_tier_manifests(namespace: str, labels: dict) -> list[dict]:
    """The "full" tier keeps the ELK stack honest (§4.2 step 3): a
    namespace-scoped Filebeat DaemonSet ships this environment's logs to the
    central Elasticsearch in the monitoring namespace. ES, Kibana and
    Logstash stay central — they are expensive and one copy serves everyone."""
    labels = {**labels, "platform.devops/tier": "full"}
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "DaemonSet",
            "metadata": {
                "name": f"{namespace}-filebeat",
                "namespace": namespace,
                "labels": {**labels, "app.kubernetes.io/name": f"{namespace}-filebeat"},
            },
            "spec": {
                "selector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": f"{namespace}-filebeat",
                    }
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app.kubernetes.io/name": f"{namespace}-filebeat",
                            "platform.devops/project": namespace,
                        }
                    },
                    "spec": {
                        "serviceAccountName": f"{namespace}-sa",
                        "containers": [
                            {
                                "name": "filebeat",
                                "image": "docker.elastic.co/beats/filebeat:8.15.0",
                                "args": [
                                    "-e",
                                    "-c",
                                    "/etc/filebeat.yml",
                                ],
                                "resources": {
                                    "requests": {"cpu": "50m", "memory": "64Mi"},
                                    "limits": {"cpu": "200m", "memory": "256Mi"},
                                },
                                "securityContext": {"runAsNonRoot": True, "allowPrivilegeEscalation": False},
                                "volumeMounts": [
                                    {"name": "config", "mountPath": "/etc/filebeat.yml", "subPath": "filebeat.yml"},
                                    {"name": "varlog", "mountPath": "/var/log", "readOnly": True},
                                    {"name": "dockersock", "mountPath": "/var/run/docker.sock", "readOnly": True},
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "config",
                                "configMap": {"name": f"{namespace}-filebeat-config"},
                            },
                            {"name": "varlog", "hostPath": {"path": "/var/log"}},
                            {"name": "dockersock", "hostPath": {"path": "/var/run/docker.sock"}},
                        ],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"{namespace}-filebeat-config",
                "namespace": namespace,
                "labels": {**labels, "app.kubernetes.io/name": f"{namespace}-filebeat"},
            },
            "data": {
                "filebeat.yml": (
                    "filebeat.inputs:\n"
                    "- type: container\n"
                    "  paths:\n"
                    "  - /var/log/containers/*.log\n"
                    "  processors:\n"
                    "  - add_kubernetes_metadata:\n"
                    "      host: ${NODE_NAME}\n"
                    "output.elasticsearch:\n"
                    "  hosts: ['http://elasticsearch.monitoring.svc.cluster.local:9200']\n"
                ),
            },
        },
    ]


def render_namespace(spec: InfraSpec, namespace: str, workspace: Path) -> Path:
    """Write the namespace manifests into the workspace and return the path."""
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "namespace.yaml"
    path.write_text(
        yaml.safe_dump_all(
            build_manifests(spec, namespace), sort_keys=False, default_flow_style=False
        )
    )
    return path
