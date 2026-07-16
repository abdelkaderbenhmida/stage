# Graph Report - .  (2026-07-12)

## Corpus Check
- Corpus is ~33,795 words - fits in a single context window. You may not need a graph.

## Summary
- 217 nodes · 212 edges · 50 communities (21 shown, 29 thin omitted)
- Extraction: 65% EXTRACTED · 35% INFERRED · 0% AMBIGUOUS · INFERRED: 75 edges (avg confidence: 0.84)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Audit & Remediation
- Platform Architecture & Phases
- Vault Client Module
- Findings Resolution & K8s Resources
- Orders Service
- Users Service
- Security Hardening
- Shared Config Module
- Products Service
- Security Validation Script
- GitOps & Canary Deployments
- Observability & SLOs
- K8s Master Bootstrap
- Docker Installation
- Cluster Status & Bootstrap
- Terraform Infrastructure
- Community 16
- Community 17
- Community 18
- Community 19
- Community 20
- Community 21
- Community 22
- Community 23
- Community 24
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29
- Community 30
- Community 31
- Community 32
- Community 33
- Community 34
- Community 35
- Community 36
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 44
- Community 45
- Community 46
- Community 47
- Community 48
- Community 49

## God Nodes (most connected - your core abstractions)
1. `Remediation Map (Finding → Fix)` - 9 edges
2. `SecretUnavailable` - 8 edges
3. `_fetch_all_secrets()` - 8 edges
4. `get_secret()` - 8 edges
5. `vault_health()` - 8 edges
6. `validate-security.sh script` - 5 edges
7. `Trivy Container Scan Job` - 5 edges
8. `DevOps Central Platform Overview (V3)` - 5 edges
9. `AppConfig` - 4 edges
10. `_vault_addr()` - 4 edges

## Surprising Connections (you probably didn't know these)
- `End-to-End Flow (git push→CI→ArgoCD→Flagger→Vault/Prometheus/ELK)` --semantically_similar_to--> `CI/CD Pipeline`  [INFERRED] [semantically similar]
  files.md/DevOps_Central_Platform_Description.md → .github/workflows/ci-cd.yml
- `Phase 4 Security Pipeline Chain (lint+gitleaks→test→build→trivy)` --semantically_similar_to--> `CI/CD Pipeline`  [INFERRED] [semantically similar]
  files.md/phase4_rapport.md → .github/workflows/ci-cd.yml
- `Least-Privilege Permissions Pattern` --semantically_similar_to--> `NetworkPolicies + PDBs (P1)`  [INFERRED] [semantically similar]
  .github/workflows/ci-cd.yml → AGENTS.md
- `Vault Root Token Out-of-Band Injection` --semantically_similar_to--> `Vault Root Token Purge (P0#1)`  [INFERRED] [semantically similar]
  files.md/findings_fixed.md → AGENTS.md
- `Semgrep SAST Job` --implements--> `Remediation Map (Finding → Fix)`  [EXTRACTED]
  .github/workflows/ci-cd.yml → AGENTS.md

## Import Cycles
- None detected.

## Communities (50 total, 29 thin omitted)

### Community 0 - "Audit & Remediation"
Cohesion: 0.10
Nodes (21): /livez vs /readyz Health Split, Vault Root Token Purge (P0#1), Fail-Closed Secret Handling (P0#3), readOnlyRootFilesystem + seccomp Hardening (P2), Remediation Map (Finding → Fix), Structured JSON Logging, Shared Library: hvac Vault Client, Shared Library: python-json-logger (+13 more)

### Community 1 - "Platform Architecture & Phases"
Cohesion: 0.11
Nodes (20): Architecture Layers (Infra→Config→Containers→Orch→Security→Deploy→Observability), DevOps Central Platform Overview (V3), End-to-End Flow (git push→CI→ArgoCD→Flagger→Vault/Prometheus/ELK), Six Platform Dimensions (IaC, Config, Containers, Security, GitOps, Observability), Phase 4: DevSecOps Pipeline Security, 7-Phase Implementation Structure, Phase 4 Security Pipeline Chain (lint+gitleaks→test→build→trivy), 9 Documented Production Incidents + Resolutions (+12 more)

### Community 2 - "Vault Client Module"
Cohesion: 0.21
Nodes (17): _fetch_all_secrets(), get_secret(), _is_vault_configured(), Vault client utility — shared by all microservices to fetch secrets dynamically, Fetch a secret by name. Resolution order:       1. Vault secret path for this se, Check Vault reachability for readiness probes.      Lightweight: uses the cached, Clear the secret cache. Used in tests / after token rotation., Raised when a required secret cannot be fetched from Vault or env.      Fail-clo (+9 more)

### Community 3 - "Findings Resolution & K8s Resources"
Cohesion: 0.14
Nodes (14): vault_client.py Duplicated 4x (P1), Per-Service ServiceAccounts (Replacing Shared SA), app/shared/ Real Python Package, Phase 3: HPA + RBAC Configuration, Incident 9: HPA Autoscaler Budget Explosion, Incident 1: OOMKill Cascade (No Resource Limits), Scan Report: metrics-server + kubectl top Working, HPA with CPU+Memory Metrics + Stabilization Windows (+6 more)

### Community 4 - "Orders Service"
Cohesion: 0.18
Nodes (7): health(), _load_secrets(), readyz(), Structured JSON logging for microservices.  Centralizing log format here means e, Configure root + service logger. Idempotent.      Args:         service_name: us, setup_logging(), Logger

### Community 5 - "Users Service"
Cohesion: 0.18
Nodes (8): health(), livez(), _load_secrets(), Resolve required secrets at startup. Raises on missing — fail closed., Liveness — process is running. Never checks deps., Readiness — must be Vault-reachable to receive production traffic.      A separa, Compatibility alias for /readyz.      Older deployments / gitleaks allowlist ref, readyz()

### Community 6 - "Security Hardening"
Cohesion: 0.20
Nodes (11): NetworkPolicies + PDBs (P1), No NetworkPolicies (P1), Phase 4 Report: Vault Deployment + Setup Job, Least-Privilege Permissions Pattern, NetworkPolicies: Default-Deny + Allow-List (6 Policies), Vault Setup Job (Enable K8s Auth, Seed KV Secrets, Create Policies), Vault Deployment (Dev Mode, vault-sa Mounted), Vault Namespace with PSA Restricted Labels (+3 more)

### Community 7 - "Shared Config Module"
Cohesion: 0.25
Nodes (6): AppConfig, Typed configuration for microservices using pydantic-settings.  Each service def, Base config — env-driven, no plaintext defaults for secrets.      Subclass and a, Build a config from environment variables. Raises on missing required., Hook for subclasses to add cross-field validation., T

### Community 8 - "Products Service"
Cohesion: 0.29
Nodes (3): health(), _load_secrets(), readyz()

### Community 9 - "Security Validation Script"
Cohesion: 0.73
Nodes (5): record_fail(), record_pass(), require_tool(), run_check(), validate-security.sh script

### Community 10 - "GitOps & Canary Deployments"
Cohesion: 0.40
Nodes (5): Flagger Canary Deployment Pattern, ArgoCD GitOps Self-Heal Pattern, Phase 5: GitOps + Canary Deployment, Incident 5: Config Drift Between Git and Cluster, Incident 6: Direct 100% Deployment Causing Outage

### Community 11 - "Observability & SLOs"
Cohesion: 0.40
Nodes (5): SLO Reference Targets (99.9% uptime, P95<200ms, error<1%), Phase 6: Full Observability Stack, Incident 4: Alert Fatigue (No SLO-Based Alerting), Define SLOs Before Alerts Principle, Incident 7: No Centralized Logs (Slow Diagnosis)

### Community 12 - "K8s Master Bootstrap"
Cohesion: 0.50
Nodes (4): Global Kubernetes Configuration Variables, Ansible Playbook k8s_master Role Assignment, K8s Sysctl Parameters, kubeadm init on Master

### Community 13 - "Docker Installation"
Cohesion: 0.50
Nodes (4): Ansible Playbook Docker Role Assignment, Docker Role Default Variables, Install Docker CE + Containerd Role, Phase 2: Ansible Configuration Automation

### Community 14 - "Cluster Status & Bootstrap"
Cohesion: 0.50
Nodes (4): Worker Fetch Join Command from Master, Bootstrap Cluster State (master+2 workers, broken API server), Scan Report: Cluster 3 Nodes Ready, Cluster Verification Commands

### Community 15 - "Terraform Infrastructure"
Cohesion: 0.50
Nodes (4): Phase 1: Terraform Infrastructure Provisioning, Auto-Generated Ansible Inventory from Terraform Outputs, libvirt Private Network 192.168.56.0/24, main.tf Line-by-Line Explanation

### Community 16 - "Community 16"
Cohesion: 0.50
Nodes (3): @opencode-ai/plugin, dependencies, @opencode-ai/plugin

### Community 17 - "Community 17"
Cohesion: 0.50
Nodes (3): plugin, $schema, .opencode/plugins/graphify.js

### Community 18 - "Community 18"
Cohesion: 0.83
Nodes (3): print_usage(), require_cmd(), bootstrap-vault-secret.sh script

### Community 19 - "Community 19"
Cohesion: 0.67
Nodes (3): CI Test Job Hardening (P0#5), CI Test Job Swallows Failures (P0#5), pip-audit Dependency Vulnerability Scanner

### Community 20 - "Community 20"
Cohesion: 0.67
Nodes (3): k8s_reset Gated by when+never Tag, Kubernetes Cluster Reset Cleanup, k8s_reset Opt-In with reset_confirmed

## Knowledge Gaps
- **81 isolated node(s):** `$schema`, `.opencode/plugins/graphify.js`, `@opencode-ai/plugin`, `devops-platform-shared`, `Lint Job` (+76 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **29 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Remediation Map (Finding → Fix)` connect `Audit & Remediation` to `Platform Architecture & Phases`, `Security Hardening`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Why does `Semgrep SAST Job` connect `Platform Architecture & Phases` to `Audit & Remediation`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Remediation Map (Finding → Fix)` (e.g. with `Remote Terraform State Backend Best Practice` and `Concurrency Cancellation Control`) actually correct?**
  _`Remediation Map (Finding → Fix)` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `get_secret()` (e.g. with `_load_secrets()` and `_load_secrets()`) actually correct?**
  _`get_secret()` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `vault_health()` (e.g. with `readyz()` and `readyz()`) actually correct?**
  _`vault_health()` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `$schema`, `.opencode/plugins/graphify.js`, `@opencode-ai/plugin` to the rest of the system?**
  _104 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Audit & Remediation` be split into smaller, more focused modules?**
  _Cohesion score 0.09523809523809523 - nodes in this community are weakly interconnected._