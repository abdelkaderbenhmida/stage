# DevOps Central Platform — Version 3 (Édition Professionnelle)

**Sécurité · GitOps · Observabilité · Incidents réels & résolutions**

---

## 1. Thème du projet

**DevOps Central Platform** est une plateforme d'infrastructure DevOps de bout en bout, construite autour de **3 microservices FastAPI** (Users, Products, Orders). L'application elle-même n'est qu'un support pédagogique : l'objectif réel du projet est de maîtriser l'ensemble de la chaîne DevOps moderne telle qu'elle est pratiquée en entreprise — provisionnement, déploiement, sécurité et supervision, entièrement automatisés et reproductibles.

Le projet ne se limite pas à faire fonctionner une architecture : il documente aussi comment cette architecture **échoue en pratique** et comment ces échecs se corrigent, à travers des scénarios d'incidents réalistes inspirés de problèmes rencontrés en production.

---

## 2. Description du projet

Le projet répond à une question simple : *comment une équipe d'ingénierie fait-elle tourner une application en production de façon fiable, sécurisée et observable, sans intervention manuelle ?*

Il couvre six grandes dimensions :

1. **Infrastructure as Code** — création automatisée et reproductible des serveurs.
2. **Configuration automatisée** — installation et paramétrage des serveurs et du cluster.
3. **Conteneurisation & orchestration** — déploiement des microservices via Kubernetes.
4. **Sécurité intégrée (DevSecOps)** — scan du code, scan des images, gestion dynamique des secrets.
5. **Déploiement automatisé (GitOps)** — synchronisation continue entre Git et le cluster, déploiements progressifs.
6. **Observabilité complète** — supervision par métriques ET par logs centralisés.

### 2.1 Objectifs pédagogiques

- Provisionner une infrastructure reproductible avec de l'Infrastructure as Code.
- Déployer des applications conteneurisées de façon orchestrée et résiliente.
- Sécuriser le pipeline de bout en bout (code, images, secrets).
- Automatiser les déploiements selon les principes du GitOps.
- Mettre en place une supervision complète (métriques + logs).
- Réduire le risque de régression en production grâce aux déploiements progressifs (Canary).
- Savoir diagnostiquer et résoudre des incidents de production réalistes.

### 2.2 Pourquoi cette approche

- Les entretiens techniques DevOps testent rarement la syntaxe d'un outil — ils testent la capacité à diagnostiquer un incident et à justifier une décision d'architecture.
- Un projet qui ne documente que des succès ne prépare pas à la réalité : la majorité du travail DevOps en entreprise est du diagnostic et de la remédiation.
- Documenter des incidents au format post-mortem (cause racine, solution, résultat mesuré) permet de les raconter tels quels en entretien, au format STAR (Situation, Tâche, Action, Résultat).

---

## 3. Architecture de la plateforme

### 3.1 Vue d'ensemble par couches

| Couche | Outils | Rôle |
|---|---|---|
| Infrastructure | Terraform | Provisionne les serveurs (VMs ou cluster managé) de façon déclarative. |
| Configuration | Ansible | Installe Docker et Kubernetes, initialise le cluster. |
| Conteneurisation | Docker | Empaquette les 3 microservices avec leurs dépendances. |
| Orchestration | Kubernetes, Helm | Déploie, fait évoluer et auto-répare les containers. |
| Sécurité | Trivy, Gitleaks, Vault | Scanne le code et les images, distribue les secrets dynamiquement. |
| Déploiement | GitHub Actions, ArgoCD, Flagger | Automatise build/test, synchronise Git↔K8s, pilote les Canary Releases. |
| Observabilité | Prometheus, Grafana, ELK Stack | Centralise métriques et logs pour le diagnostic et l'alerting. |

### 3.2 Flux de bout en bout

```
git push
  │
  ▼
GitHub Actions: lint → Gitleaks → tests → build Docker → Trivy → push registry
  │
  ▼
ArgoCD détecte le commit ── synchronise l'état désiré vers Kubernetes
  │
  ▼
Flagger: 10% du trafic vers la nouvelle version ── analyse 5 min ── 100% ou rollback
  │
  ├── Pods récupèrent leurs secrets ──────────────► Vault
  ├── Métriques exposées /metrics ───────────────► Prometheus ──► Grafana
  └── Logs collectés par Filebeat ───────────────► Logstash ──► Elasticsearch ──► Kibana
```

### 3.3 Structure du dépôt Git

```
devops-central-platform/
├── terraform/{main,variables,outputs}.tf
├── ansible/roles/{docker,k8s_common,k8s_master,k8s_worker}/
├── app/                       # 3 microservices FastAPI + Dockerfile
├── k8s/
│   ├── apps/                  # Deployments, Services, HPA, RBAC
│   ├── monitoring/{prometheus,grafana,elk}/
│   ├── argocd/applications/
│   ├── vault/
│   └── canary/                # Flagger + Istio
├── .github/workflows/ci-cd.yml
└── scripts/validate-platform.sh
```

---

## 4. Stack technique complète

| Outil | Catégorie | Rôle précis |
|---|---|---|
| **Terraform** | IaC | Provisionne les VMs (master + workers), réseau et firewall. |
| **Ansible** | Configuration | Installe Docker + Kubernetes, initialise et joint le cluster. |
| **Docker** | Conteneurisation | Containerise chaque microservice avec ses dépendances. |
| **Kubernetes** | Orchestration | Déploie, scale (HPA) et auto-répare les Pods. |
| **Helm** | Packaging K8s | Déploie Prometheus, Grafana et ELK via des charts paramétrables. |
| **Prometheus** | Monitoring | Collecte les métriques système et applicatives toutes les 15s. |
| **Grafana** | Visualisation | Dashboards temps réel (infra, applicatif, sécurité). |
| **GitHub Actions** | CI/CD | Pipeline : lint → tests → scan → build → push → sync. |
| **Trivy** | Sécurité (image) | Scanne chaque image Docker à la recherche de CVE critiques. |
| **Gitleaks** | Sécurité (code) | Détecte les secrets accidentellement commités. |
| **HashiCorp Vault** | Gestion des secrets | Distribue des identifiants temporaires aux applications. |
| **ArgoCD** | GitOps | Synchronise en continu Kubernetes avec l'état déclaré dans Git. |
| **Flagger + Istio** | Déploiement progressif | Pilote les Canary Releases et le rollback automatique. |
| **ELK Stack** | Logs centralisés | Elasticsearch (stockage), Logstash (traitement), Kibana (recherche). |
| **AlertManager** | Alerting | Route les alertes Prometheus selon la sévérité. |

---

## 5. Modules clés

### 5.1 Sécurité (DevSecOps)

Le pipeline intègre la sécurité à chaque étape plutôt qu'en fin de cycle :

```
lint → Gitleaks (secrets) → tests → build → Trivy (CVE) → push → ArgoCD sync
```

**HashiCorp Vault** remplace les secrets statiques (mots de passe en clair dans des fichiers `.env`) par des identifiants **temporaires**, récupérés dynamiquement par les applications au démarrage et renouvelés automatiquement. Ainsi, même si le dépôt de code est compromis, les secrets actifs ne le sont pas.

### 5.2 Déploiement GitOps & progressif

**ArgoCD** fait de Git la seule source de vérité : tout changement doit passer par une pull request, et ArgoCD synchronise automatiquement le cluster avec ce qui est déclaré dans Git. Toute modification manuelle du cluster qui s'écarte de Git est détectée et peut être corrigée automatiquement (self-heal).

**Flagger**, combiné à **Istio**, permet des **Canary Deployments** : une nouvelle version n'est d'abord exposée qu'à une petite fraction du trafic (ex. 10%). Ses métriques d'erreur et de latence sont analysées automatiquement avant d'augmenter progressivement le trafic, ou de revenir en arrière en cas de problème.

### 5.3 Observabilité

- **Prometheus + Grafana** répondent à *"combien d'erreurs ?"* — métriques, tendances, alerting basé sur les 4 Golden Signals (latence, trafic, erreurs, saturation).
- **ELK Stack** répond à *"quelle erreur exactement, où, pourquoi ?"* — logs détaillés, recherche full-text, contexte pour l'investigation.

---

## 6. SLOs de référence

| Indicateur (SLI) | Objectif (SLO) | Source de mesure |
|---|---|---|
| Disponibilité du service | ≥ 99.9% sur 30 jours | Prometheus |
| Latence P95 | < 200 ms | Prometheus |
| Taux d'erreur 5xx | < 1% | Prometheus / Grafana |
| MTTD (temps de détection) | < 2 minutes | AlertManager |
| MTTR (temps de résolution) | < 30 minutes | Kibana + post-mortems |

---

## 7. Glossaire

| Terme | Définition |
|---|---|
| **IaC** | Infrastructure as Code — décrire l'infrastructure sous forme de code versionnable. |
| **GitOps** | Approche où Git est la source de vérité unique pour l'état désiré du système. |
| **CI/CD** | Intégration et déploiement continus. |
| **DevSecOps** | Intégration de la sécurité directement dans le pipeline DevOps. |
| **Canary Deployment** | Déploiement progressif exposant une nouvelle version à une fraction du trafic. |
| **Config drift** | Divergence entre l'état déclaré (Git) et l'état réel d'un système. |
| **CVE** | Identifiant standardisé d'une faille de sécurité connue. |
| **SLI / SLO / SLA** | Indicateur de service / Objectif associé / Engagement contractuel. |
| **MTTD / MTTR** | Temps moyen de détection / de résolution d'un incident. |

---

## 8. Compétences démontrées

| Compétence | Poste(s) visé(s) |
|---|---|
| Infrastructure as Code & GitOps | Cloud Engineer, DevOps Engineer |
| Diagnostic d'incidents & post-mortems | SRE, DevOps Engineer senior |
| Sécurisation du pipeline (DevSecOps) | DevSecOps Engineer, Security Engineer |
| Observabilité (métriques + logs) | SRE, Observability Engineer |
| Déploiement progressif & rollback | Release Engineer, Platform Engineer |
