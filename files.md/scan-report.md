# Rapport — Scan complet du projet DevOps Central Platform

---

## 1. État de l'infrastructure

**VMs : 3/3 Opérationnelles**

| VM | IP | Statut | OS | vCPU | RAM | Disque utilisé |
|---|---|---|---|---|---|---|
| master-01 | 192.168.56.10 | Running | Ubuntu 22.04.5 | 2 | 2G | 7.2G/20G |
| worker-01 | 192.168.56.11 | Running | Ubuntu 22.04.5 | 2 | 2G | 5G/20G |
| worker-02 | 192.168.56.12 | Running | Ubuntu 22.04.5 | 2 | 2G | 5.6G/20G |

- Réseau `devops-platform-net` (192.168.56.0/24) actif + autostart
- Pool de stockage `default` : 91G disponibles / 196G total
- SSH `devops@<ip>` fonctionnel sur les 3 nœuds (clé ed25519)
- `ansible all -m ping` → pong × 3

---

## 2. État du cluster Kubernetes

**Cluster Sain — 3 nœuds Ready, 22h uptime (depuis dernier boot)**

```
NAME        STATUS   ROLES           VERSION
master-01   Ready    control-plane   v1.28.15
worker-01   Ready    <none>          v1.28.15
worker-02   Ready    <none>          v1.28.15
```

**Pods critiques :**
- `kube-apiserver`, `kube-controller-manager`, `kube-scheduler` → Running (restart 12-14x = post-boot normal)
- `etcd-master-01` → Running
- `coredns` → 2 replicas Running
- `kube-proxy` → Running sur 3 nœuds
- `calico-node` → Running sur 3 nœuds
- `calico-typha`, `calico-kube-controllers`, `tigera-operator` → Running
- `metrics-server` → Running (`kubectl top nodes` fonctionnel)

**Validation API/DNS :**
- `/healthz` → ok
- `cs` (scheduler, controller-manager, etcd-0) → Healthy
- DNS `nslookup kubernetes.default.svc.cluster.local` → 10.96.0.1 (OK)

---

## 3. Microservices Application (Phase 3)

**3 services dans `devops-platform` namespace — tous Running**

| Service | Replicas | /health | /service | /metrics |
|---|---|---|---|---|
| users-service | 2/2 | OK | OK | OK |
| products-service | 2/2 | OK | OK | OK |
| orders-service | 2/2 | OK | OK | OK |

**Tests passés :**
- Test HTTP depuis pod temporaire → responses OK pour les 3 services
- Métriques Prometheus exposées (`/metrics`)
- Images Docker 56MB présentes sur master (`crictl images`) — `users-service:latest`, `products-service:latest`, `orders-service:latest`

---

## 4. Self-Healing & Resilience

**Suppression Pod → recréation < 30s : OK**

```
POD DELETED: users-service-7fc4f9d8b6-mcnfq
  ↓ 25s wait
NEW POD:     users-service-7fc4f9d8b6-kcfsf   Running   AGE=27s
```

---

## 5. HPA (Horizontal Pod Autoscaler)

**3 HPA actifs dans `devops-platform`**

| HPA | Min | Max | CPU cible | CPU actuel | Replicas |
|---|---|---|---|---|---|
| users-service | 2 | 5 | 70% | 2% | 2 |
| products-service | 2 | 5 | 70% | 2% | 2 |
| orders-service | 2 | 5 | 70% | 2% | 2 |

- HPA fonctionne (`kubectl get hpa` montre métriques CPU via metrics-server)

---

## 6. RBAC

- ServiceAccount `devops-platform-sa` créé dans `devops-platform`
- Role `devops-platform-role` (get/list/watch pods, services, configmaps, deployments)
- RoleBinding `devops-platform-rolebinding` lié → OK

---

## 7. État des fichiers — Validation statique

| Artefact | Fichiers | Validation |
|---|---|---|
| **Terraform** | main.tf, variables.tf, outputs.tf, backend.tf, cloud-init.tpl, inventory.tpl, network-config.tpl | `terraform validate` → Success. State liste 15 ressources (VMs, volumes, network, cloud-init disks, inventory) |
| **Ansible** | 4 rôles (docker, k8s_common, k8s_master, k8s_worker) + k8s_reset | `ansible-playbook --syntax-check` → OK. Dép. warnings: `apt_repository` → `deb822_repository` (cosmétique, pas bloquant). Tags `never` corrects. `ansible all --list-hosts` → 3 hôtes |
| **App Python** | users/main.py, products/main.py, orders/main.py (24 lignes chacun) | `ast.parse()` → Syntax OK, toutes routes `/`, `/health`, `/users/...`, `/metrics` présentes |
| **Dockerfiles** | 3 fichiers multi-stage (builder + final, python:3.11-slim) | Build `users-service` testé → OK. `/health` + `/users` répondent |
| **K8s manifests** | 5 YAMLs (users/orders/products-deployment.yaml, hpa.yaml, rbac.yaml) | Python `yaml.safe_load_all()` → OK. Deployments + Service intégrés dans chaque YAML. `imagePullPolicy: Never` → cohérent avec images locales |

---

## 8. Configurations — Revue point par point

### Terraform — Sans anomalies
- Pas de backend distant (local seulement) — Incident 8 documenté dans `files.md/` sur le risque de conflits Terraform, mais OK pour single-dev
- Base image : `/var/lib/libvirt/images/ubuntu-22.04-server-cloudimg-amd64.img` (664MB) → présente
- Bug connu `content.url` ignore `capacity` → contourné via cloud-init `growpart`/`resizefs` — OK

### Ansible — Sans anomalies
- `SystemdCgroup = true` confirmé sur master (`sudo grep`)
- `containerd.sock` → `/run/containerd/containerd.sock`
- Calico v3.26.1 installé via Tigera operator (pas manifest statique) → meilleure pratique
- kube-proxy + coredns installés post-init (skip-phases contourne timeout API-server)
- Idempotency guards sur `kubeadm init` (`creates: /etc/kubernetes/admin.conf`) et join (`creates: /etc/kubernetes/kubelet.conf`)

### K8s manifests — Points notables
- Manifests combinés (Deployment + Service dans même fichier) pour chaque microservice → OK
- `imagePullPolicy: Never` → dépend des images pré-chargées sur master (vrai ici)
- Limits/requests présents (CPU 100m/250m, RAM 128Mi/256Mi) → OK
- Probes readiness + liveness présents → OK
- Pas de namespace `devops-platform` dans les manifests (créé manuellement via `kubectl create ns`) → normal, RBAC included

---

## 9. Ce qui est encore PLANIFIÉ mais PAS implémenté

| Module | Fichiers attendus | Statut |
|---|---|---|
| **Monitoring** (Prometheus/Grafana/ELK) | `k8s/monitoring/` | Dir vide, pas de manifests |
| **ArgoCD** (GitOps) | `k8s/argocd/` | Dir vide |
| **Vault** (secrets) | `k8s/vault/` | Dir vide |
| **Canary/Flagger+Istio** | `k8s/canary/` | Dir vide |
| **CI/CD** (.github/workflows) | `ci-cd.yml` | Non créé |
| **Validation script** | `scripts/validate-platform.sh` | Non créé |
| **Gitleaks** config | `.gitleaks.toml` | Non créé |
| **Trivy** scan config | — | Non créé |
| **.gitignore** | `.gitignore` | Non créé |

**Phases complétées :** Phase 1 (Terraform) + Phase 2 (Ansible) + Phase 3 (Microservices + K8s manifests).
**Phases restantes :** Phase 4 (Sécurité — Trivy/Gitleaks/Vault), Phase 5 (GitOps — ArgoCD/Flagger/Istio), Phase 6 (Observabilité — Prometheus/Grafana/ELK), Phase 7 (Validation).

---

## 10. Anomalies corrigées durant le scan

| Problème | Résolution |
|---|---|
| VMs éteintes (`shut off`) | `virsh start` → 3 VMs Running |
| VMs inaccessibles (`No route to host`) | Ping + SSH fonctionnels après boot |
| Host `kubectl` contexte dead (`minikube`) | Fetch `admin.conf` master, merge dans `~/.kube/config`, `use-context` → `kubectl get nodes` OK |
| Restarts élevés (12-14x) sur control-plane pods | Post-boot normal (VMs éteintes, etcd re-sync au démarrage) — stable après 5min |
| `imagePullPolicy: Never` + pas de registry externe | Confirmé: images présentes sur master via `crictl images` — 3 images 56MB OK |
| Stockage pool 91G disponible | OK pour l'instant, mais à surveiller si on ajoute ELK/Prometheus data |
| RAM master-01 à 77% (1436Mi/2048Mi) | Pas de MemoryPressure dans kubelet — OK pour l'instant, mais scaling futur à considérer |
| Ansible `apt_repository` deprecated (Ansible core ≥ 2.21) | Pas bloquant, fonctionne encore — à migrer vers `deb822_repository` |

---

## 11. Résumé — Checkpoints passés

```
✅ 01 — Terraform config valide, resources 15 dans state
✅ 02 — Ansible playbook syntax OK, 5 rôles prêts
✅ 03 — 3 VMs (1 master + 2 workers) bootées, SSH OK
✅ 04 — containerd SystemdCgroup=true, Docker + K8s 1.28 installés
✅ 05 — Cluster Kubernetes 3 nœuds Ready
✅ 06 — API server répond (/healthz OK, cs Healthy)
✅ 07 — CoreDNS résout (nslookup → 10.96.0.1)
✅ 08 — Calico CNI OK (calico-node 3/3, typha 2, kube-controllers 1)
✅ 09 — 3 microservices (users/products/orders) 6 pods Running
✅ 10 — Services Internes répondent (/health, /users, /products, /orders, /metrics)
✅ 11 — HPA 3 services actifs, metrics-server → kubectl top nodes OK
✅ 12 — Self-healing test: Kill Pod → neuf Pod créé en 27s
✅ 13 — Images Docker 56MB présentes sur master, build local OK
✅ 14 — RBAC (SA + Role + RoleBinding) déployé
✅ 15 — Host kubectl config sync → nativement fonctionnel
✅ 16 — App Python syntax valide, Dockerfiles multi-stage valides
✅ 17 — K8s manifests YAML syntax valide
────────────────────────────────────────────────────────────
⚠️  18 — Pas de CI/CD (.github/workflows/) — Phase 4 manquante
❌ 19 — Pas de monitoring (Prometheus/Grafana/ELK) — Phase 6 manquante
❌ 20 — Pas de GitOps (ArgoCD/Flagger/Istio) — Phase 5 manquante
❌ 21 — Pas de sécurité pipeline (Trivy/Gitleaks/Vault) — Phase 4 manquante
❌ 22 — Pas de script validation (validate-platform.sh) — Phase 7 manquante
❌ 23 — Pas de .gitignore/.gitleaks.toml — fichiers non créés
```

---

## Verdict global

**Phases 1-2-3 : FULLY OPERATIONAL.** Infrastructure (Terraform) provisionnée, Configuration (Ansible) déployée, Kubernetes 1.28 cluster 3 nœuds fonctionnel, 3 microservices FastAPI conteneurisés et déployés avec HPA + RBAC + probes + resources limits.

**Phases 4-5-6-7 : NON IMPLÉMENTÉES.** Pipeline CI/CD, DevSecOps (Trivy/Gitleaks/Vault), GitOps (ArgoCD), Canary (Flagger/Istio), Observabilité (Prometheus/Grafana/ELK), Validation script. Ce sont les prochaines étapes à créer.

Note terminale : `network libvirt devops-platform-net` marqué autostart — VMs survivront reboot hôte. Host `kubectl` désormais prêt à interagir avec le cluster sans SSH.
