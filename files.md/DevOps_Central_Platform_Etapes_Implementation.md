# DevOps Central Platform — Guide d'implémentation étape par étape

Ce guide détaille, phase par phase, comment construire la plateforme de A à Z. Chaque phase indique l'objectif, les étapes concrètes, des exemples de commandes, et un critère de validation avant de passer à la suivante.

---

## Prérequis

- Une machine hôte avec au moins 8 Go de RAM et 4 vCPU disponibles (ou un compte cloud AWS/GCP/Azure en free tier).
- Outils installés en local : `git`, `terraform`, `ansible`, `docker`, `kubectl`, `helm`.
- Un compte GitHub (ou GitLab) pour héberger le dépôt et exécuter le pipeline CI/CD.
- Un compte Docker Hub ou GHCR pour héberger les images construites.

---

## Phase 1 — Infrastructure avec Terraform

**Objectif :** provisionner 3 serveurs (1 master + 2 workers) de façon déclarative et reproductible.

1. Initialiser le projet Terraform :
   ```bash
   mkdir terraform && cd terraform
   terraform init
   ```
2. Définir les ressources serveurs dans `main.tf` (VMs ou instances cloud), avec 2 vCPU / 2 Go RAM minimum par nœud.
3. Paramétrer les valeurs modifiables (nombre de workers, image OS, taille) dans `variables.tf` plutôt que de les coder en dur.
4. Configurer un réseau privé dédié entre les nœuds (ex. `192.168.56.0/24` en local).
5. Ajouter un `outputs.tf` qui expose les adresses IP des serveurs créés.
6. Exécuter le provisionnement :
   ```bash
   terraform plan     # vérifier ce qui va être créé
   terraform apply    # créer réellement les ressources
   ```
7. Générer automatiquement l'inventaire Ansible à partir des IPs de sortie (via un template `inventory.tpl`).

**Critère de validation :** `terraform output` affiche les 3 adresses IP, et une connexion SSH manuelle vers chacune fonctionne.

> **Bonne pratique :** ne jamais stocker le fichier d'état (`terraform.tfstate`) uniquement en local dans une équipe à plusieurs — utiliser un backend distant avec verrouillage (ex. S3 + DynamoDB) pour éviter les conflits d'exécutions simultanées.

---

## Phase 2 — Configuration avec Ansible

**Objectif :** installer et configurer Docker et Kubernetes sur les 3 serveurs, sans intervention manuelle.

1. Écrire `inventory.ini` listant les 3 serveurs (1 groupe `masters`, 1 groupe `workers`).
2. Vérifier la connectivité :
   ```bash
   ansible all -m ping
   ```
   → doit retourner `pong` pour chacun des 3 hôtes.
3. Créer un rôle **docker** : installation de Docker CE, activation du service, ajout de l'utilisateur au groupe `docker`.
4. Créer un rôle **k8s_common** appliqué à tous les nœuds :
   - désactivation du swap (`swapoff -a`),
   - activation des modules noyau nécessaires (`br_netfilter`, `overlay`),
   - installation de `kubeadm`, `kubelet`, `kubectl`.
5. Créer un rôle **k8s_master** (nœud master uniquement) :
   - initialisation du cluster : `kubeadm init --pod-network-cidr=<CIDR>`,
   - déploiement du réseau CNI (ex. Calico),
   - génération de la commande de jonction (`kubeadm token create --print-join-command`).
6. Créer un rôle **k8s_worker** : exécution de la commande de jonction générée à l'étape précédente sur chaque worker.

**Critère de validation :**
```bash
kubectl get nodes
```
→ les 3 nœuds apparaissent avec le statut `Ready`.

---

## Phase 3 — Conteneurisation des microservices

**Objectif :** empaqueter les 3 microservices FastAPI (Users, Products, Orders) en images Docker prêtes à déployer.

1. Écrire le code de chaque microservice (routes API, logique métier minimale, endpoint `/health` et `/metrics`).
2. Créer un `Dockerfile` multi-stage pour chaque service :
   - étape 1 : installation des dépendances Python,
   - étape 2 : image finale allégée avec uniquement le code et les dépendances nécessaires.
3. Construire et tester chaque image en local :
   ```bash
   docker build -t users-service:local ./app/users
   docker run -p 8000:8000 users-service:local
   ```
4. Vérifier que chaque service répond correctement sur son endpoint de santé avant de continuer.
5. Créer les manifests Kubernetes de base (`Deployment`, `Service`) pour chaque microservice, avec 2 replicas.
6. **Définir systématiquement `resources.requests` et `resources.limits`** sur chaque container (évite qu'un service surchargé ne monopolise toute la mémoire d'un nœud).
7. Ajouter des `readinessProbe` et `livenessProbe` précises sur chaque container.

**Critère de validation :**
```bash
kubectl get pods -n devops-platform
```
→ tous les Pods sont `Running` avec 2/2 ou 1/1 containers prêts.

---

## Phase 4 — Sécurité du pipeline (DevSecOps)

**Objectif :** intégrer la sécurité comme étape bloquante du pipeline, pas comme vérification a posteriori.

1. Installer Gitleaks et le tester en local :
   ```bash
   gitleaks detect --source . -v
   ```
2. Ajouter Gitleaks comme première étape du pipeline CI (avant même les tests) — tout secret détecté doit faire échouer le job immédiatement.
3. Ajouter Trivy juste après l'étape de build Docker :
   ```bash
   trivy image --severity CRITICAL,HIGH --exit-code 1 <image>
   ```
   Le paramètre `--exit-code 1` fait échouer le pipeline si une faille critique est trouvée.
4. Déployer HashiCorp Vault dans le cluster (via Helm ou manifests dédiés).
5. Initialiser et débloquer (unseal) Vault, puis configurer un moteur de secrets (ex. KV v2).
6. Modifier les microservices pour récupérer leurs identifiants (base de données, clés API) dynamiquement depuis Vault au démarrage, au lieu de variables d'environnement statiques.

**Critère de validation :**
- `gitleaks detect` → aucun secret détecté.
- `trivy image <image>` → 0 vulnérabilité critique.
- `vault status` → `Sealed: false`.

---

## Phase 5 — Déploiement GitOps & progressif

**Objectif :** faire de Git la seule source de vérité pour l'état du cluster, et sécuriser les mises en production.

1. Installer ArgoCD dans le cluster :
   ```bash
   kubectl create namespace argocd
   kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
   ```
2. Déclarer une ressource `Application` ArgoCD par microservice, pointant vers le dossier correspondant du dépôt Git.
3. Activer la synchronisation automatique et le mode `self-heal` (ArgoCD corrige automatiquement tout écart entre Git et le cluster).
4. Installer Istio (service mesh) puis Flagger :
   ```bash
   istioctl install --set profile=demo
   helm install flagger flagger/flagger --namespace istio-system
   ```
5. Définir, pour chaque microservice, un objet `Canary` Flagger avec :
   - pourcentage de trafic initial (ex. 10%),
   - seuil d'erreur maximum toléré (ex. taux d'erreur 5xx < 1%),
   - durée d'analyse avant d'augmenter le trafic (ex. 5 minutes par palier).

**Critère de validation :**
```bash
argocd app get <nom-app>
```
→ statut `Synced` et `Healthy`. Un déploiement de test avec une régression volontaire doit déclencher un rollback automatique de Flagger.

---

## Phase 6 — Observabilité complète

**Objectif :** pouvoir répondre à la fois à *"combien d'erreurs ?"* (métriques) et *"quelle erreur exactement ?"* (logs).

1. Déployer Prometheus via Helm, avec un intervalle de scraping de 15 secondes.
2. Exposer un endpoint `/metrics` sur chaque microservice (compteurs de requêtes, latence, erreurs).
3. Déployer Grafana et connecter Prometheus comme source de données.
4. Construire au moins 3 dashboards : vue d'ensemble infrastructure, performance applicative, taux d'erreurs.
5. Déployer la stack ELK (Elasticsearch, Logstash, Kibana) via Helm.
6. Installer Filebeat en DaemonSet sur chaque nœud pour collecter les logs de tous les Pods.
7. Construire les vues Kibana nécessaires à la recherche rapide d'erreurs par service.
8. Configurer AlertManager avec des règles basées sur des **SLOs mesurables** (et non sur chaque métrique disponible) :
   - disponibilité < 99.9 % sur 30 jours,
   - latence P95 > 200 ms,
   - taux d'erreur 5xx > 1 %.

**Critère de validation :**
- Prometheus (`localhost:9090` → Targets) : tous les targets en `UP`.
- Grafana (`localhost:3000`) : les 3 dashboards affichent des données en temps réel.
- Kibana (`localhost:5601`) : les logs des 3 microservices sont recherchables.

---

## Phase 7 — Validation finale de la plateforme

**Objectif :** vérifier que l'ensemble de la chaîne fonctionne de bout en bout, automatiquement.

1. Écrire un script `scripts/validate-platform.sh` qui exécute successivement toutes les vérifications ci-dessus.
2. Simuler un incident volontaire (ex. suppression d'un Pod) et vérifier le self-healing :
   ```bash
   kubectl delete pod <nom-pod>
   kubectl get pods -w
   ```
   → le Pod doit être recréé automatiquement en moins de 30 secondes.
3. Simuler une régression applicative et vérifier que Flagger déclenche un rollback automatique.
4. Documenter le résultat de chaque test dans un résumé final :

```
✅ PASS — Cluster Kubernetes opérationnel
✅ PASS — Pods en statut Running
✅ PASS — Aucune vulnérabilité critique (Trivy)
✅ PASS — Aucun secret détecté (Gitleaks)
✅ PASS — ArgoCD synchronisé
✅ PASS — Dashboards Grafana actifs
✅ PASS — Logs indexés dans Kibana
────────────────────────────────
7/7 tests passés — Projet VALIDÉ
```

---

## Résumé des phases

| Phase | Objectif principal | Durée indicative |
|---|---|---|
| 1. Infrastructure | Provisionner les serveurs | 1–2 jours |
| 2. Configuration | Installer Docker + Kubernetes | 1–2 jours |
| 3. Applications | Conteneuriser et déployer les microservices | 2–3 jours |
| 4. Sécurité | Intégrer Trivy, Gitleaks, Vault | 2–3 jours |
| 5. GitOps & Canary | Installer ArgoCD, Flagger | 2 jours |
| 6. Observabilité | Prometheus, Grafana, ELK | 2–3 jours |
| 7. Validation | Tests de bout en bout | 1 jour |
