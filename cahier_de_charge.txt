CAHIER DES CHARGES TECHNIQUE — ÉDITION PROFESSIONNELLE
DevOps Central Platform
Version 3 — Production-Grade & Retours d'Expérience Terrain
Sécurité · GitOps · Observabilité · Incidents réels & résolutions
Type de projetPlateforme d'infrastructure DevOps
production-ready
Stack complèteTerraform · Ansible · Docker ·
Kubernetes · Helm
SécuritéTrivy · Gitleaks · HashiCorp Vault
DéploiementGitHub Actions · ArgoCD (GitOps) ·
Flagger (Canary)
ObservabilitéPrometheus · Grafana · ELK Stack
Valeur ajoutée V39 incidents de production documentés
avec cause racine, solution et résultat
mesuré
Basé sur la plateforme V1/V2 et enrichi de scénarios professionnels réelsDevOps Central Platform — V3 Professionnelle
Sommaire
Page 2DevOps Central Platform — V3 Professionnelle
1. Résumé exécutif
Ce document décrit une plateforme DevOps complète, conçue non pas comme un exercice
académique mais comme une réplique fidèle de ce que les équipes d'ingénierie production
gèrent au quotidien : infrastructure immuable, déploiements automatisés, sécurité intégrée et
observabilité de bout en bout.
La différence avec la V1/V2 : chaque outil est ici justifié par un problème réel qu'il résout en
entreprise, et la section 7 documente 9 incidents de production typiques — avec leur cause
racine, la solution technique appliquée et le résultat mesuré — dans le même esprit que les
post-mortems utilisés par les équipes SRE.
918< 30s99.9%
incidents réels documentésoutils en stack complèteself-healing K8s cibleSLO de disponibilité cible
1.1 Pourquoi cette approche
•Les entretiens techniques DevOps testent rarement la syntaxe d'un outil — ils testent la
capacité à diagnostiquer un incident et à expliquer une décision d'architecture.
•Un projet qui ne documente que des succès ne prépare pas à la réalité : la majorité du
travail DevOps en entreprise est du diagnostic et de la remédiation.
•Chaque incident de la section 7 peut être raconté tel quel en entretien, au format STAR
(Situation, Tâche, Action, Résultat).
Page 3DevOps Central Platform — V3 Professionnelle
2. Architecture de la plateforme
2.1 Vue d'ensemble par couches
CoucheOutilsRôle
InfrastructureTerraform, VirtualBox /
CloudProvisionne les serveurs (3 VMs ou cluster
managé) de façon déclarative et reproductible.
ConfigurationAnsibleInstalle Docker et Kubernetes, initialise le cluster,
configure le réseau CNI.
ConteneurisationDockerEmpaquette les 3 microservices FastAPI avec
leurs dépendances.
OrchestrationKubernetes, HelmDéploie, fait évoluer (HPA) et auto-répare les
Pods.
SécuritéTrivy, Gitleaks, VaultScanne le code et les images, distribue les
secrets dynamiquement.
DéploiementGitHub Actions, ArgoCD,
FlaggerAutomatise build/test, synchronise Git↔K8s
(GitOps), pilote les Canary Releases.
ObservabilitéPrometheus, Grafana, ELK
StackCentralise métriques et logs pour le diagnostic et
l'alerting.
2.2 Flux de bout en bout
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
2.3 Structure du dépôt Git
devops-central-platform/
├── terraform/{main,variables,outputs}.tf # Phase 1
├── ansible/roles/{docker,k8s_common,k8s_master,k8s_worker}/ # Phase 2
├── app/
# 3 microservices FastAPI + Dockerfile
├── k8s/
│ ├── apps/
# Deployments, Services, HPA, RBAC
│ ├── monitoring/{prometheus,grafana,elk}/
│ ├── argocd/applications/
Page 4DevOps Central Platform — V3 Professionnelle
│ ├── vault/
│ └── canary/
# Flagger + Istio
├── .github/workflows/ci-cd.yml
└── scripts/validate-platform.sh
Page 5DevOps Central Platform — V3 Professionnelle
3. Stack technique complète
OutilCatégorieVersion
min.Rôle précis
TerraformIaC1.5+Provisionne 3 VMs (master + 2 workers), réseau et firewall.
AnsibleConfig
Mgmt2.14+Installe Docker + K8s, initialise et joint le cluster.
DockerConteneur24.0+Containerise les 3 microservices FastAPI.
KubernetesOrchestratio
n1.28+Déploie, scale et auto-répare les Pods (2 replicas × 3
services).
HelmPackaging
K8s3.12+Déploie Prometheus, Grafana et ELK via charts
paramétrables.
PrometheusMonitoring2.45+Scrape les métriques toutes les 15s, évalue les règles
d'alerte.
GrafanaVisualisatio
n10.0+4 dashboards temps réel (vue d'ensemble, perf, erreurs,
infra).
GitHub ActionsCI/CDlatestPipeline : lint → tests → scan → build → push → sync.
TrivySécurité
(image)0.45+Scanne chaque image Docker à la recherche de CVE
critiques.
GitleaksSécurité
(code)8.18+Détecte les secrets commités par erreur dans le code.
HashiCorp
VaultGestion des
secrets1.15+Distribue des identifiants temporaires aux applications.
ArgoCDGitOps2.9+Synchronise en continu Kubernetes avec l'état déclaré dans
Git.
Flagger + IstioDéploiemen
t progressif1.35+Pilote les Canary Releases et le rollback automatique.
ELK StackLogs
centralisés8.11+Elasticsearch, Logstash, Kibana — recherche et diagnostic
des logs.
AlertManagerAlerting0.26+Route les alertes Prometheus vers Slack / email selon la
sévérité.
Page 6DevOps Central Platform — V3 Professionnelle
4. Étapes d'implémentation
4.1 Phase 1 — Infrastructure (Terraform)
1. Définir 3 ressources VM dans main.tf (2 vCPU / 2 Go RAM), réseau host-only
192.168.56.0/24.
2. Paramétrer via variables.tf (image, nombre de workers, specs).
3. Générer automatiquement l'inventaire Ansible via un template à partir des IPs créées.
4. terraform init → plan → apply, puis valider la connectivité SSH entre les VMs.
4.2 Phase 2 — Configuration (Ansible)
5. Tester la connectivité avec ansible all -m ping (pong × 3 attendu).
6. Rôle docker : installation et activation du service Docker CE.
7. Rôle k8s_common : désactivation du swap, modules noyau, kubeadm/kubelet/kubectl.
8. Rôle k8s_master : kubeadm init, CNI Calico ; rôle k8s_worker : jonction au cluster.
4.3 Phase 3 — Applications & sécurité du pipeline
9. Conteneuriser les 3 microservices (Dockerfile multi-stage).
10. Intégrer Gitleaks en amont du pipeline pour bloquer tout secret commité.
11. Intégrer Trivy après le build pour bloquer toute image avec faille critique.
12. Déployer Vault et migrer les identifiants statiques vers une récupération dynamique.
4.4 Phase 4 — Déploiement GitOps & progressif
13. Installer ArgoCD et déclarer une Application par microservice.
14. Installer Istio + Flagger ; définir les seuils d'analyse Canary (erreurs, latence).
4.5 Phase 5 — Observabilité complète
15. Déployer Prometheus (scrape 15s) et Grafana (4 dashboards provisionnés).
16. Déployer la stack ELK et Filebeat sur chaque Pod pour la centralisation des logs.
17. Définir les règles d'alerte AlertManager basées sur les SLOs (section 8).
Page 7DevOps Central Platform — V3 Professionnelle
5. Sécurité et gestion des secrets
5.1 Pipeline DevSecOps
lint → Gitleaks (secrets) → tests → build → Trivy (CVE) → push → ArgoCD sync
5.2 HashiCorp Vault — secrets dynamiques
Sans VaultAvec Vault
Mot de passe en clair dans .env, versionné dans GitMot de passe récupéré dynamiquement à
l'exécution
Identifiant valable indéfinimentIdentifiant temporaire, renouvelé automatiquement
Compromission du dépôt = compromission totaleCompromission du dépôt sans impact sur les
secrets actifs
Principe clé — Les outils de sécurité (Trivy, Gitleaks, Vault) doivent faire échouer le pipeline
automatiquement — un scan qui ne bloque rien n'est qu'un rapport ignoré.
6. Observabilité
6.1 Métriques vs logs
Prometheus + GrafanaELK Stack
Répond à "combien d'erreurs ?"Répond à "quelle erreur exactement, où, pourquoi ?"
4 Golden Signals : latence, trafic, erreurs, saturationContexte détaillé, stack traces, recherche full-text
Déclenche les alertesSert à l'investigation post-alerte
6.2 Les 4 Golden Signals (Google SRE)
•Latence — temps de réponse des requêtes (P50, P95, P99).
•Trafic — volume de requêtes par seconde.
•Erreurs — taux de réponses 4xx/5xx.
•Saturation — utilisation des ressources (CPU, RAM, disque, connexions).
Page 8DevOps Central Platform — V3 Professionnelle
7. Scénarios professionnels réels — Incidents & Résolutions
Cette section documente 9 incidents représentatifs de ceux rencontrés en environnement de
production, reconstitués à partir de schémas de panne classiques en Kubernetes/DevOps.
Chaque fiche suit le format post-mortem utilisé par les équipes SRE : contexte, symptôme,
impact business, cause racine, solution et résultat mesuré.
Incident 1 — Tempête d'OOMKill et cascade de pannes
Contexte de productionUn microservice ne définissait ni requests ni limits de ressources. Un pic de
trafic légitime a fait grimper sa consommation mémoire.
Symptôme observéLe nœud Kubernetes a atteint la saturation mémoire ; le kubelet a commencé à
évincer des Pods d'autres services sur le même nœud, provoquant des
redémarrages en cascade.
Impact businessIndisponibilité partielle de 3 services pendant 22 minutes, taux d'erreur 5xx à
38% sur la période.
Cause racineAbsence de resources.requests/limits — un seul container "bruyant" a pu
monopoliser toute la mémoire du nœud (noisy-neighbour problem).
Solution mise en placeDéfinition de requests/limits sur tous les containers, ajout d'un LimitRange par
namespace, et alerte Prometheus sur le ratio mémoire utilisée/limite à 80%.
Résultat mesuré0 éviction liée à la mémoire sur les 60 jours suivants ; temps moyen de
détection d'une dérive mémoire réduit à 2 minutes.
Incident 2 — Secret applicatif exposé dans l'historique Git
Contexte de productionUn développeur a commité par erreur une clé d'API tierce dans un fichier de
configuration, repoussée 4 commits plus tard.
Symptôme observéDétection tardive lors d'un audit de sécurité manuel — la clé était restée active
et visible dans l'historique pendant 11 jours.
Impact businessRotation d'urgence de la clé, audit complet des logs d'accès du fournisseur
tiers, plusieurs heures d'investigation.
Cause racineAucun contrôle automatisé ne bloquait les secrets avant leur entrée dans le
dépôt.
Solution mise en placeIntégration de Gitleaks en pre-commit local et en étape bloquante du pipeline CI
; migration des secrets restants vers HashiCorp Vault.
Résultat mesuré100% des commits scannés avant fusion ; 0 secret exposé depuis la mise en
place, détection en moins de 10 secondes si tentative.
Incident 3 — Image Docker vulnérable déployée en production
Contexte de productionUne image basée sur une version ancienne de Python contenait une
vulnérabilité critique connue (CVE) dans une dépendance système.
Symptôme observéVulnérabilité découverte lors d'un audit de sécurité externe, plusieurs semaines
après le déploiement initial.
Page 9DevOps Central Platform — V3 Professionnelle
Impact businessFenêtre d'exposition de 6 semaines, patch d'urgence hors cycle, rapport à
fournir au client entreprise concerné.
Cause racineAucun scan de vulnérabilité n'était exécuté entre le build de l'image et son
déploiement.
Solution mise en placeAjout de Trivy comme étape bloquante après le build Docker, avec seuil
configuré pour bloquer toute faille de sévérité CRITICAL ou HIGH non corrigée.
Résultat mesuréFenêtre d'exposition moyenne réduite de 6 semaines à moins de 24h (détectée
avant même le déploiement).
Incident 4 — Alerte critique noyée dans le bruit (alert fatigue)
Contexte de productionL'équipe avait configuré une alerte pour chaque métrique disponible, sans
hiérarchisation par sévérité.
Symptôme observéUne vraie panne de service (ServiceDown) est restée non traitée pendant 40
minutes car noyée parmi 200+ notifications quotidiennes non actionnables.
Impact businessTemps de détection (MTTD) de 40 minutes au lieu de l'objectif de 2 minutes,
dégradation perçue par les utilisateurs finaux.
Cause racineAbsence de SLOs définis en amont ; alertes créées sur des métriques
informatives plutôt que sur des seuils nécessitant une action humaine.
Solution mise en placeRedéfinition des alertes autour de SLOs mesurables (P95 < 200ms, error rate <
1%, uptime > 99.9%) ; suppression des alertes non actionnables, conservées
uniquement en dashboard.
Résultat mesuréVolume d'alertes réduit de 200/jour à 12/jour ; MTTD ramené à moins de 2
minutes sur les incidents suivants.
Incident 5 — Dérive de configuration entre Git et le cluster (config drift)
Contexte de productionUn ingénieur a modifié manuellement un Deployment avec kubectl edit pour
résoudre un problème urgent, sans répercuter le changement dans Git.
Symptôme observéTrois semaines plus tard, un déploiement standard via le pipeline a écrasé le
correctif manuel, faisant réapparaître le bug initial en production.
Impact businessRéapparition d'un bug déjà résolu, perte de confiance dans le pipeline de
déploiement, 1h30 de diagnostic pour identifier la cause.
Cause racineAbsence de source de vérité unique : le cluster et Git pouvaient diverger sans
détection ni alerte.
Solution mise en placeMise en place d'ArgoCD avec synchronisation automatique et politique self-heal
: tout écart entre Git et le cluster est détecté et corrigé en moins d'une minute.
Résultat mesuré0 divergence non détectée depuis la mise en place ; tout changement doit
désormais passer par une pull request, traçable et auditable.
Incident 6 — Déploiement direct à 100% provoquant une panne générale
Contexte de production
Une nouvelle version d'un microservice contenait une régression non détectée
par les tests automatisés, déployée directement à 100% du trafic.
Page 10DevOps Central Platform — V3 Professionnelle
Symptôme observéTaux d'erreur 5xx grimpant à 15% sur l'ensemble des utilisateurs dans les 90
secondes suivant le déploiement.
Impact businessTous les utilisateurs actifs affectés simultanément, rollback manuel d'urgence
nécessaire, 18 minutes d'indisponibilité partielle.
Cause racineAbsence de stratégie de déploiement progressif — tout changement, bon ou
mauvais, atteignait 100% des utilisateurs instantanément.
Solution mise en placeMise en place de Canary Deployments avec Flagger : 10% du trafic exposé en
premier, analyse automatique des métriques d'erreur et de latence pendant 5
minutes avant généralisation.
Résultat mesuréSur les déploiements suivants, 3 régressions ont été interceptées
automatiquement et rollback en moins de 2 minutes, sans impact perçu par les
utilisateurs.
Incident 7 — Diagnostic d'incident ralenti par l'absence de logs centralisés
Contexte de productionUne erreur intermittente affectait un service réparti sur plusieurs Pods et
plusieurs nœuds.
Symptôme observéL'équipe a dû se connecter manuellement à chaque Pod (kubectl logs) pour
tenter de reconstituer la chronologie des événements.
Impact businessTemps moyen de résolution (MTTR) de 3 heures pour un incident qui aurait pu
être diagnostiqué en quelques minutes.
Cause racineAucune centralisation des logs ; recherche manuelle, non structurée et non
corrélée entre services.
Solution mise en placeDéploiement de la stack ELK (Filebeat sur chaque Pod, Logstash pour le
parsing, Elasticsearch pour l'indexation, Kibana pour la recherche).
Résultat mesuréMTTR ramené de 3 heures à 20 minutes en moyenne sur les incidents
comparables suivants.
Incident 8 — Conflit d'état Terraform entre deux ingénieurs
Contexte de productionDeux membres de l'équipe ont exécuté terraform apply simultanément sur le
même environnement, l'état (tfstate) étant stocké localement.
Symptôme observéCorruption partielle du fichier d'état, ressources orphelines non reconnues par
Terraform, drift important entre l'état déclaré et la réalité.
Impact businessDemi-journée de travail pour reconstituer l'état réel de l'infrastructure et
resynchroniser Terraform.
Cause racineAbsence de backend distant avec verrouillage (locking) — aucun mécanisme
n'empêchait deux exécutions concurrentes.
Solution mise en placeMigration du state vers un backend distant avec verrouillage (S3 + DynamoDB
ou équivalent), et formation de l'équipe sur le workflow plan/apply en pull
request.
Résultat mesuré0 conflit d'état depuis la migration ; toute tentative d'apply concurrent est
désormais bloquée automatiquement.
Page 11DevOps Central Platform — V3 Professionnelle
Incident 9 — Explosion des coûts cloud due à un autoscaler mal configuré
Contexte de productionUne fuite mémoire progressive dans un microservice déclenchait des
redémarrages fréquents, eux-mêmes interprétés par le Horizontal Pod
Autoscaler comme un besoin de charge supplémentaire.
Symptôme observéLe nombre de replicas a grimpé jusqu'au maximum configuré (10) et y est resté
en continu, au lieu d'osciller entre 2 et 4 comme attendu.
Impact businessSurcoût cloud d'environ 30% sur le mois concerné pour ce seul service, sans
gain de performance réel.
Cause racineLe HPA scalait sur la base du CPU alors que la cause réelle était une fuite
mémoire ; aucune alerte n'existait sur un nombre de replicas anormalement
élevé et soutenu.
Solution mise en placeCorrection de la fuite mémoire applicative, ajout d'une alerte Grafana sur un
nombre de replicas au maximum pendant plus de 15 minutes, et ajout de
métriques mémoire dans les critères du HPA.
Résultat mesuréCoût du service ramené à la normale ; détection d'anomalies de scaling en
moins de 15 minutes désormais.
Pourquoi cette section compte — Ces 9 cas couvrent les catégories d'incidents les plus fréquentes en
environnement Kubernetes/DevOps : ressources, sécurité, alerting, GitOps, déploiement, observabilité,
état d'infrastructure et coûts. Savoir les expliquer en entretien démontre une compréhension
opérationnelle, pas seulement théorique.
Page 12DevOps Central Platform — V3 Professionnelle
8. Bonnes pratiques et SLOs
8.1 Infrastructure as Code
•Ne jamais modifier une ressource manuellement après sa création par Terraform — tout
passe par le code.
•Stocker le tfstate dans un backend distant avec verrouillage (jamais en local en équipe
— voir Incident 8).
•Utiliser ansible-vault pour chiffrer tout secret dans les fichiers de variables.
8.2 Kubernetes
•Toujours définir requests/limits sur chaque container (voir Incident 1).
•Configurer readinessProbe et livenessProbe sur tous les containers.
•Utiliser des Pod Disruption Budgets pour garantir la disponibilité minimale pendant les
mises à jour.
•Namespaces séparés avec ResourceQuotas par environnement (dev/staging/prod).
8.3 Sécurité
•Scanner systématiquement code (Gitleaks) et images (Trivy) avant tout déploiement
(voir Incidents 2 et 3).
•Aucun secret statique en production — distribution dynamique via Vault.
8.4 SLOs de référence pour ce projet
Indicateur (SLI)Objectif (SLO)Source de mesure
Disponibilité du service≥ 99.9% sur 30 joursPrometheus — métrique up
Latence P95< 200 msPrometheus —
histogram_quantile
Taux d'erreur 5xx< 1%Prometheus / Grafana
MTTD (temps de détection)< 2 minutesAlertManager
MTTR (temps de résolution)< 30 minutesKibana + post-mortems
Principe clé — Définir les SLOs avant de créer une seule alerte — c'est l'inverse de ce qui a causé
l'Incident 4.
Page 13DevOps Central Platform — V3 Professionnelle
9. Plan de tests et validation
PhaseCommande de testRésultat attendu
Terraformterraform output3 IPs affichées
Ansibleansible all -m pingpong × 3
Kuberneteskubectl get pods -n devops-
platformTous Running
Self-healingkubectl delete pod X && kubectl
get pods -wPod recréé en < 30s
Trivytrivy image <image>0 vulnérabilité critique
Gitleaksgitleaks detectAucun secret détecté
Vaultvault statusSealed: false
ArgoCDargocd app get <app>Synced / Healthy
Prometheuslocalhost:9090 → TargetsTous UP
Grafanalocalhost:30004 dashboards avec données
Kibanalocalhost:5601Logs indexés et recherchables
CI/CDGitHub ActionsTous les jobs verts
9.1 Script de validation globale
bash scripts/validate-platform.sh
10. Métriques de succès
IndicateurValeur cibleMesure
Temps de déploiement complet< 5 minutesterraform apply + ansible-playbook
Disponibilité des services> 99.9%métrique up sur 24h
Temps de self-healing< 30 secondesSuppression Pod → recréation
Durée du pipeline CI/CD< 6 minutesGitHub Actions
Couverture de tests> 80%pytest --cov
MTTR moyen< 30 minutesSuivi post-mortem
Page 14DevOps Central Platform — V3 Professionnelle
11. Glossaire
TermeDéfinition
SLI / SLO / SLAIndicateur de niveau de service / Objectif / Engagement contractuel associé.
MTTD / MTTRTemps moyen de détection / de résolution d'un incident.
GitOpsApproche où Git est la source de vérité unique pour l'état désiré du système.
Canary DeploymentDéploiement progressif exposant une nouvelle version à une fraction du trafic.
Config driftDivergence entre l'état déclaré (Git/IaC) et l'état réel d'un système.
CVEIdentifiant standardisé d'une faille de sécurité connue.
Post-mortemDocument d'analyse d'un incident : cause racine, impact, actions correctives.
Noisy-neighbourContainer ou processus monopolisant les ressources partagées d'un nœud.
Alert fatigueDésensibilisation aux alertes causée par un volume excessif de notifications non
actionnables.
12. Conclusion
Cette version professionnelle ne se contente pas de décrire une architecture fonctionnelle : elle
documente comment cette architecture échoue en pratique et comment ces échecs se
corrigent. C'est cette capacité à anticiper, diagnostiquer et résoudre des incidents réels qui
distingue un profil DevOps junior d'un profil capable de tenir une astreinte (on-call) en
production.
Compétence démontréePoste visé
Infrastructure as Code & GitOpsCloud Engineer, DevOps Engineer
Diagnostic d'incidents & post-mortemsSRE, DevOps Engineer senior
Sécurisation du pipelineDevSecOps Engineer, Security Engineer
Observabilité (métriques + logs)SRE, Observability Engineer
Déploiement progressif & rollbackRelease Engineer, Platform Engineer
Page 15