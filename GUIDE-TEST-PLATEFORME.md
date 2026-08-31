# Guide de test complet — plateforme DevOps

Ce guide fait passer chaque outil du rapport, de Terraform à l'observabilité :
la commande à lancer, ce qu'on doit voir en retour, et **quand ouvrir quelle
interface web** pour vérifier visuellement.

## Deux environnements — ne pas les confondre

| | Cluster applicatif | Homelab infra |
|---|---|---|
| Nature | `kind` local (1 nœud) | 2 VMs libvirt (`master-01`, `worker-01`) |
| Sert à tester | K8s app-layer, Vault, ArgoCD, Prometheus/Grafana/ELK, les 5 microservices | Terraform, Ansible, cloud-init, libvirt/KVM |
| Accès | `kubectl` (contexte `kind-devops-platform`) | `ssh devops@192.168.56.10` puis `kubectl` sur place |

---

## Table des interfaces web — ouvrir quand indiqué plus bas

| Outil | Port-forward à lancer | URL | Identifiants |
|---|---|---|---|
| ArgoCD | `kubectl port-forward -n argocd svc/argocd-server 8480:80 &` | http://localhost:8480 | `admin` / `kubectl get secret -n argocd argocd-initial-admin-secret -o jsonpath='{.data.password}' \| base64 -d` |
| Grafana | `kubectl port-forward -n monitoring svc/grafana 3000:80 &` | http://localhost:3000 | `kubectl get secret -n monitoring grafana -o jsonpath='{.data.admin-user}' \| base64 -d` puis `...admin-password...` |
| Prometheus | `kubectl port-forward -n monitoring svc/prometheus-server 9090:80 &` | http://localhost:9090 | aucun |
| AlertManager | `kubectl port-forward -n monitoring svc/prometheus-alertmanager 9093:9093 &` | http://localhost:9093 | aucun |
| Kibana | `kubectl port-forward -n monitoring svc/kibana 5601:5601 &` | http://localhost:5601 | `elastic` / `kubectl get secret -n monitoring elasticsearch-credentials -o jsonpath='{.data.ELASTIC_PASSWORD}' \| base64 -d` |
| Swagger (users-service) | `kubectl port-forward -n devops-platform svc/users-service 18080:80 &` | http://localhost:18080/docs | aucun |
| GitHub Actions | — | `https://github.com/<org>/<repo>/actions` | ton compte GitHub |
| GHCR (images publiées) | — | `https://github.com/<org>/<repo>/pkgs/container/<service>` | ton compte GitHub |

Lance tous les port-forwards en une fois avant de commencer si tu veux tout
avoir sous la main :
```bash
kubectl port-forward -n argocd svc/argocd-server 8480:80 &
kubectl port-forward -n monitoring svc/grafana 3000:80 &
kubectl port-forward -n monitoring svc/prometheus-server 9090:80 &
kubectl port-forward -n monitoring svc/prometheus-alertmanager 9093:9093 &
kubectl port-forward -n monitoring svc/kibana 5601:5601 &
kubectl port-forward -n devops-platform svc/users-service 18080:80 &
```

---

## 1. Infrastructure as Code (homelab)

```bash
cd terraform
terraform init
terraform version
terraform plan       # 0 to add si le homelab tourne déjà
```
🖥 **Rien à voir en interface ici** — Terraform et Ansible sont 100% CLI.
```bash
terraform apply      # ⚠ détruit/recrée les 2 VMs si déjà debout — ne relance pas sans raison
```

```bash
virsh list --all
virsh net-dumpxml devops-platform-net
virsh dominfo master-01
```

### cloud-init
```bash
python3 -c "
import yaml
t = open('terraform/cloud-init.tpl').read()
r = t.replace('\${hostname}','x').replace('\${ssh_user}','devops').replace('\${ssh_public_key}','k')
yaml.safe_load(r); print('YAML valide')"

ssh devops@192.168.56.10 'cloud-init status'
ssh root@192.168.56.10 'true'            # doit être refusé
ssh devops@192.168.56.10 'sudo -n true'  # doit demander un mot de passe
```

### Ansible
```bash
cd ansible
ansible -i inventory.ini all -m ping
ansible-playbook -i inventory.ini playbook.yml --list-tasks
ansible-playbook -i inventory.ini playbook.yml   # 4-5 min, PLAY RECAP failed=0
```
🖥 **Toujours rien en interface.** Vérification en CLI sur les nœuds :
```bash
ssh devops@192.168.56.10 'kubectl get nodes -o wide'          # 2 Ready
ssh devops@192.168.56.10 'kubectl get pods -n calico-system'
ssh devops@192.168.56.10 'apt-mark showhold'
```

---

## 2. Conteneurisation et orchestration (cluster applicatif)

```bash
kubectl config use-context kind-devops-platform

docker build -t users-service:1.0.0 -f app/users-service/Dockerfile app/
docker inspect --format='{{.Config.User}}' users-service:1.0.0   # appuser
docker run -d --name t -e ENVIRONMENT=dev -e LOG_FORMAT=plain -p 18099:8000 users-service:1.0.0
# sans ENVIRONMENT=dev : le conteneur refuse de demarrer (fail-closed, pas de Vault local)
sleep 20 && docker ps --filter name=t                             # (healthy)
docker rm -f t

ssh devops@192.168.56.10 'sudo crictl ps'   # containerd, sur le homelab

kubectl get nodes -o wide
kubectl kustomize k8s/apps/overlays/dev  | grep -c 'replicas: 1'
kubectl kustomize k8s/apps/overlays/prod | grep -E 'replicas:'
kubectl get networkpolicy -n devops-platform
kubectl get hpa -n devops-platform
```
🖥 **Rien à ouvrir pour Docker/containerd/Kustomize** — inspection CLI
uniquement, ce sont des outils sans UI.

🖥 **Kubernetes n'a pas d'UI dédiée dans ce projet** (pas de dashboard K8s
installé) — tout se lit via `kubectl`. La vue « graphique » du cluster vient
plus tard via **ArgoCD** (arbre de ressources, §6) et **Grafana** (§7).

---

## 3. Application et gestion des secrets

```bash
kubectl port-forward -n devops-platform svc/users-service 18080:80 &
curl -s localhost:18080/ | python3 -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' localhost:18080/readyz   # 200
```
🖥 **Ouvre maintenant : http://localhost:18080/docs**
C'est le Swagger UI généré par FastAPI. Regarde : les endpoints `/`, `/livez`,
`/readyz`, `/users` listés, clique **Try it out** sur `/users` pour exécuter
une requête directement depuis le navigateur.

```bash
kubectl exec -n vault deploy/vault -- vault status   # sealed: false
```
🖥 **Vault n'a pas d'UI activée dans ce projet** (mode dev, API seulement) —
tout passe par `vault` CLI / `kubectl exec`.

**Fail-closed (réversible)** :
```bash
kubectl scale deploy/vault -n vault --replicas=0
sleep 45
curl -s -o /dev/null -w '%{http_code}\n' localhost:18080/readyz   # 503
kubectl scale deploy/vault -n vault --replicas=1
kubectl rollout status deploy/vault -n vault
```
🖥 Pendant que Vault est arrêté, tu peux regarder l'effet dans **Grafana**
(dashboard *Error Rate*, voir §7) et dans **Kibana** (les logs `vault.fetch`,
voir §7) — c'est le meilleur moment pour ouvrir ces deux-là.

---

## 4. Qualité et sécurité (ce que la CI exécute, reproductible en local)

```bash
ruff check app/shared/ app/users-service/
pip-audit -r app/shared/requirements.txt --strict
gitleaks detect --source . --config .gitleaks.toml --redact
pre-commit run --all-files
kubectl kustomize k8s/apps/base | kubeconform -strict -ignore-missing-schemas -summary
kubectl kustomize k8s/apps/base | conftest test --policy k8s/policies/conftest -
trivy image --severity CRITICAL,HIGH users-service:1.0.0
kubectl get clusterpolicy   # Kyverno, si installé
```
🖥 **Aucune interface** — ruff, pip-audit, Gitleaks, pre-commit, kubeconform,
Conftest, Trivy et Kyverno sont tous des outils en ligne de commande. Leurs
résultats remontent visuellement plus tard dans **GitHub Actions** (§5).

---

## 5. Chaîne CI/CD (GitHub Actions)

```bash
git push origin secondary
gh run watch
```
🖥 **Ouvre maintenant : `https://github.com/<org>/<repo>/actions`**
Regarde le run se dérouler en direct :
1. **Vue graphe** du run → dépendances entre jobs (Lint, Tests, Gitleaks,
   Build&Push ×5 en matrice, Trivy ×5, écriture du digest).
2. Clique sur un job **Container Scan (Trivy)** → onglet résumé → table
   CRITICAL/HIGH.
3. Clique sur **Build & Push Images** → logs → section `docker/metadata-action`
   pour voir les tags générés (`commit-<sha>`).

🖥 **Ouvre ensuite : `https://github.com/<org>/<repo>/pkgs/container/<service>`**
→ les images publiées, avec leurs tags.

Le déploiement manuel (`workflow_dispatch`) ne se déclenche qu'une fois
l'environnement `production` protégé par un reviewer (*Settings →
Environments*) — c'est là que GitHub affichera la demande d'approbation.

---

## 6. GitOps (ArgoCD)

```bash
kubectl port-forward -n argocd svc/argocd-server 8480:80 &
kubectl get applications -n argocd -o custom-columns=\
NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
```
🖥 **Ouvre maintenant : http://localhost:8480** (identifiants dans la table
du haut).
- Écran d'accueil : les 20 tuiles Applications, toutes `Synced` / `Healthy`.
- Clique sur **users-service** → onglet arbre de ressources : Deployment →
  ReplicaSets → Pods, en direct.
- Onglet **History and Rollback** : chaque révision passée, cliquable pour
  revenir en arrière.

**Preuve self-heal** — regarde ArgoCD pendant que tu tapes :
```bash
kubectl scale deploy/users-service -n devops-platform --replicas=5
```
🖥 Dans l'UI ArgoCD, `users-service` passe `OutOfSync` quelques secondes puis
revient tout seul à `Synced` (le nombre de répliques est ramené à 2). C'est
le moment le plus parlant à observer en direct dans le navigateur.

---

## 7. Observabilité et SLO

```bash
kubectl port-forward -n monitoring svc/prometheus-server 9090:80 &
kubectl port-forward -n monitoring svc/grafana 3000:80 &
kubectl port-forward -n monitoring svc/prometheus-alertmanager 9093:9093 &
kubectl port-forward -n monitoring svc/kibana 5601:5601 &
```

🖥 **Ouvre : http://localhost:9090/targets**
Tous les jobs doivent être `UP` (vert). C'est ici qu'on vérifie que Prometheus
scrape bien les 5 services + kubelet + kube-state-metrics.

🖥 **Ouvre : http://localhost:9090/graph** → onglet Graph, tape par exemple
`sum(rate(http_requests_total[5m])) by (service)` → **Execute** → bascule sur
l'onglet **Graph** pour voir la courbe.

🖥 **Ouvre : http://localhost:3000** (identifiants dans la table du haut)
Trois dashboards à parcourir dans l'ordre :
- **Infrastructure Overview** — nœuds, pods, CPU/mémoire du cluster.
- **Application Performance** — RPS par endpoint, latence p95/p99. Lance
  `./scripts/stress-hpa.sh` dans un autre terminal et regarde la courbe
  monter en direct sur ce dashboard.
- **Error Rate SLO** — taux de 5xx avec le seuil à 1 % tracé.

🖥 **Ouvre : http://localhost:9093**
Liste des alertes actives. Après le test de charge ci-dessus, si le HPA reste
au plafond 15 minutes, `HPAPinnedAtMaxReplicas` doit apparaître ici.

🖥 **Ouvre : http://localhost:5601** (identifiants `elastic` dans la table)
- Menu ☰ → **Discover** → sélectionne l'index `devops-platform-*` →
  histogramme des logs sur la dernière heure.
- Filtre `app.event: "vault.fetch_failed"` pour retrouver les logs de
  l'incident Vault du §3 si tu l'as joué.

---

## 8. Scripts de validation globale

```bash
./scripts/validate-platform.sh
./scripts/validate-platform.sh --ci
./scripts/validate-security.sh
./scripts/smoke-test.sh
./scripts/stress-hpa.sh
```
🖥 Pas d'interface propre à ces scripts — mais c'est le moment idéal pour
avoir **Grafana** et **AlertManager** déjà ouverts dans deux onglets à côté du
terminal, pour voir l'effet de `stress-hpa.sh` en direct pendant qu'il tourne.

---

## 9. Scénarios d'incident (réversibles) — et où regarder pendant chacun

| # | Déclencheur | CLI à lancer | Interface à ouvrir pendant l'incident |
|---|---|---|---|
| 1 | commit → déploiement | `git push origin secondary` | GitHub Actions (run) puis ArgoCD (sync) |
| 2 | panne Vault | `kubectl scale deploy/vault --replicas=0` | Grafana *Error Rate* + Kibana *Discover* |
| 3 | fuite mémoire | déployer un pod limité à 64Mi qui alloue en boucle | Grafana *Infrastructure Detail* + AlertManager |
| 4 | montée en charge | `./scripts/stress-hpa.sh` | Grafana *Application Performance* + `kubectl get hpa -w` |
| 5 | régression bloquée | pousser un handler cassé | GitHub Actions (job rouge) — jamais dans ArgoCD, bloqué avant |
| 6 | rotation de secret | `scripts/bootstrap-vault-secret.sh` | Kibana (rolling restart des pods dans les logs) |
| 7 | run nocturne | déclenchement planifié | GitHub Actions (Gitleaks/Trivy) |

---

## Checklist finale rapide

```bash
kubectl get nodes && kubectl get pods -A --no-headers | grep -v Running | grep -v Completed
kubectl get applications -n argocd --no-headers | grep -v "Synced.*Healthy"
curl -s localhost:9090/api/v1/targets | grep -c '"health":"down"'
./scripts/validate-platform.sh --ci
```
Sortie vide/zéro partout + exit 0 = plateforme saine du provisionnement à
l'observabilité, aussi bien en CLI qu'en interface.
