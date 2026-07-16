devops-central-platform/
│
├── terraform/                          # Phase 1 — Infrastructure
│   ├── main.tf                         # Définition des VMs (master + workers)
│   ├── variables.tf                    # Paramètres (nb workers, specs, image OS)
│   ├── outputs.tf                      # IPs des serveurs créés
│   ├── inventory.tpl                   # Template pour générer l'inventaire Ansible
│   └── backend.tf                      # Configuration du state distant (S3/DynamoDB)
│
├── ansible/                             # Phase 2 — Configuration
│   ├── inventory.ini                   # Liste des serveurs (masters/workers)
│   ├── ansible.cfg
│   ├── playbook.yml                    # Playbook principal
│   └── roles/
│       ├── docker/tasks/main.yml       # Installation Docker
│       ├── k8s_common/tasks/main.yml   # kubeadm, kubelet, kubectl, swap off
│       ├── k8s_master/tasks/main.yml   # kubeadm init, CNI Calico
│       └── k8s_worker/tasks/main.yml   # Jonction au cluster
│
├── app/                                 # Phase 3 — Microservices
│   ├── users-service/
│   │   ├── main.py                     # Endpoints FastAPI + /health + /metrics
│   │   ├── requirements.txt
│   │   └── Dockerfile                  # Build multi-stage
│   ├── products-service/
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── orders-service/
│       ├── main.py
│       ├── requirements.txt
│       └── Dockerfile
│
├── k8s/                                  # Manifests Kubernetes
│   ├── apps/
│   │   ├── users-deployment.yaml       # Deployment + requests/limits + probes
│   │   ├── users-service.yaml
│   │   ├── products-deployment.yaml
│   │   ├── products-service.yaml
│   │   ├── orders-deployment.yaml
│   │   ├── orders-service.yaml
│   │   ├── hpa.yaml                    # Horizontal Pod Autoscaler
│   │   └── rbac.yaml
│   │
│   ├── monitoring/                      # Phase 6 — Observabilité
│   │   ├── prometheus/values.yaml      # Config Helm Prometheus
│   │   ├── grafana/
│   │   │   ├── values.yaml
│   │   │   └── dashboards/             # JSON des 3+ dashboards
│   │   ├── elk/
│   │   │   ├── elasticsearch-values.yaml
│   │   │   ├── logstash-values.yaml
│   │   │   ├── kibana-values.yaml
│   │   │   └── filebeat-daemonset.yaml
│   │   └── alertmanager/rules.yaml     # Règles d'alerte basées sur les SLOs
│   │
│   ├── vault/                           # Phase 4 — Sécurité
│   │   ├── vault-values.yaml
│   │   └── vault-policy.hcl
│   │
│   ├── argocd/                          # Phase 5 — GitOps
│   │   └── applications/
│   │       ├── users-app.yaml
│   │       ├── products-app.yaml
│   │       └── orders-app.yaml
│   │
│   └── canary/                          # Phase 5 — Déploiement progressif
│       ├── istio-gateway.yaml
│       ├── users-canary.yaml           # Objet Canary Flagger
│       ├── products-canary.yaml
│       └── orders-canary.yaml
│
├── .github/
│   └── workflows/
│       └── ci-cd.yml                    # lint → Gitleaks → tests → build → Trivy → push
│
├── scripts/
│   ├── validate-platform.sh            # Phase 7 — Script de validation globale
│   └── generate-inventory.sh
│
├── docs/
│   ├── DevOps_Central_Platform_Description.md
│   └── DevOps_Central_Platform_Etapes_Implementation.md
│
├── .gitleaks.toml                       # Config des règles Gitleaks
├── .gitignore
└── README.md