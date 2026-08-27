# DevOps Central Platform

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A **multi-tenant, self-service deployment platform**. A user signs up, creates a
project, points it at a Git repository, and the platform builds the code, scans the
image for vulnerabilities, refuses it if the scan fails, deploys it into an isolated
Kubernetes namespace, and hands back a URL — with metrics, logs, quotas and a lifetime
after which the environment is destroyed automatically.

Several tenants share one cluster and cannot see, reach, or delete each other's work.

The repository contains three things: the **control plane** (the platform itself), the
**infrastructure** it manages (Terraform, Ansible, Kubernetes, monitoring, Vault,
ArgoCD), and a set of **demo microservices** that the platform operates as its own
workload.

> The original specification documents in [`docs/`](docs/) are written in French:
> [`DevOps_Central_Platform_Description.md`](docs/DevOps_Central_Platform_Description.md)
> and [`DevOps_Central_Platform_Etapes_Implementation.md`](docs/DevOps_Central_Platform_Etapes_Implementation.md).

---

## Contents

- [Where each user's code lives](#where-each-users-code-lives)
- [What the platform does](#what-the-platform-does)
- [Architecture](#architecture)
- [The deployment pipeline](#the-deployment-pipeline)
- [Provisioning modes](#provisioning-modes)
- [Tenancy and access control](#tenancy-and-access-control)
- [Security model](#security-model)
- [Observability](#observability)
- [API reference](#api-reference)
- [Data model](#data-model)
- [Background jobs](#background-jobs)
- [Repository layout](#repository-layout)
- [Toolchain](#toolchain)
- [Getting started](#getting-started)
- [Configuration reference](#configuration-reference)
- [Scripts](#scripts)
- [Testing](#testing)
- [CI/CD](#cicd)
- [Operations](#operations)
- [Status and limits](#status-and-limits)

---

## Where each user's code lives

This is the question the whole design turns on, so it comes first: **the platform
stores no tenant source code at all.**

A deployment holds a *pointer*, never a copy:

```text
repo_url   = https://github.com/some-org/their-service.git
branch     = main
image_ref  = registry/<team>/<project>-<service>:commit-abc1234
```

At deploy time the worker shallow-clones the repository into a throwaway directory,
builds an image, scans it, pushes it to the registry, **removes `.git`**, and then
deletes the clone. What persists is the *image*. The source stays wherever its owner
keeps it.

Three things are easy to confuse:

| | Holds | Where it lives |
| --- | --- | --- |
| **Tenant code** | a user's application source | their own Git repository (external) |
| **Workspaces** | Terraform state, generated inventories, rendered manifests | `WORKSPACE_ROOT`, one directory per project |
| **`app/`** | the platform's *own* demo microservices | this repository — **not** tenant code |

That last row is the usual misreading: `app/users-service`, `app/orders-service` and
friends belong to the platform and are managed from the operator console. No tenant's
code ever lands there.

`.git` removal matters more than it looks: the next pipeline step builds an image from
that directory, so a tenant `Dockerfile` doing `COPY . .` would otherwise copy the
remote configuration — and, for an authenticated clone, the credential used to fetch
it — into an image that is then pushed to a registry.

---

## What the platform does

### For a tenant

| Capability | Detail |
| --- | --- |
| **Projects** | An environment you own. Either a quota-bounded namespace on the shared cluster, or a set of real VMs built with Terraform and configured with Ansible. |
| **Deployments** | Build and ship any Git repository. Public by default; private repositories work once the team stores a token. Strategies: plain `deployment`, `canary`, or `bluegreen` (Argo Rollouts). |
| **Security scanning** | On-demand Trivy (container images), Gitleaks (committed secrets) and pip-audit (Python dependencies), with findings stored and summarised per severity. |
| **Pre-deploy gate** | Every image is scanned before it reaches the cluster. Any CRITICAL or HIGH blocks the rollout, and so does a scan that cannot be read. |
| **Metrics** | CPU, memory, running pods and container restarts for your own namespace, over a bounded time window. |
| **Logs** | Recent log lines from Loki for your own namespace, with an optional search term. |
| **Catalogue** | One page answering "what is running, who owns it, is it healthy, is it safe" across every team you belong to. |
| **Teams** | Invite members with roles, see per-team cost estimates, and store the git credential used for private repositories. |
| **Lifetimes** | Every environment carries a TTL, can be extended up to a ceiling, and is reaped automatically when it expires. |
| **Webhooks** | A push to the deployed branch can trigger a redeploy; GitHub and GitLab signatures are verified. |

### For an operator

An admin-only console (`Operations`) over the platform's own infrastructure:

- **Health and overview** — service inventory, pipeline stage per service, degraded-state detection
- **Apps and services** — scaffold or remove the platform's own microservices; the CI matrix and ArgoCD applications follow automatically
- **Live cluster** — pods, events, per-pod logs, restarts, rollout history and undo
- **ArgoCD** — application status, sync, refresh, per-application resource tree
- **Vault** — status, per-service secrets, provisioning and resync
- **CI** — recent runs, logs, trigger, re-run and cancel
- **Infrastructure** — Terraform state, drift detection, reconciliation, capacity and preflight checks
- **Alerts and logs** — AlertManager state and history, pipeline log search

---

## Architecture

```text
                        ┌──────────────────────────────┐
      browser ─────────▶│  console (static SPA)        │
                        │  workspace + operator halves │
                        └──────────────┬───────────────┘
                                       │ REST + JWT
                        ┌──────────────▼───────────────┐
                        │  FastAPI control plane       │
                        │  routers → repositories      │◀── tenancy enforced here
                        └───┬───────────────────┬──────┘
                            │                   │
                  PostgreSQL│                   │Redis ── Celery broker
                  (state)   │                   │
                        ┌───▼───────────────────▼──────┐
                        │  Celery workers              │
                        │  provision · deploy · scan   │
                        │  destroy · reap              │
                        └───┬──────────────────────────┘
                            │ every command runs inside a sandbox container
        ┌───────────────────┼────────────────────┬─────────────────┐
        ▼                   ▼                    ▼                 ▼
   Terraform +         docker build         Trivy / Gitleaks    kubectl
   Ansible (VM mode)   + registry push      / pip-audit         (namespace mode)
        │                   │                    │                 │
        ▼                   ▼                    ▼                 ▼
    tenant VMs          image registry       findings in DB    tenant namespace
                                                               (quota, netpol, SA)
```

Supporting services: **Vault** (tenant secrets, per-service credentials),
**Prometheus + Grafana** (metrics), **Loki / ELK** (logs), **AlertManager** (SLO
alerts), **ArgoCD** (GitOps for the platform's own services).

---

## The deployment pipeline

Every step streams into a job log the tenant can watch live.

| Step | What happens | Failure behaviour |
| --- | --- | --- |
| **1/9 clone** | Shallow clone of the requested branch. No credentials unless the team configured one. `.git` is removed afterwards. | A private or missing repository, or a branch that does not exist, fails in seconds with a message naming the repository — not a git internal error. |
| **2/9 secret scan + gate** | Gitleaks scans the checkout for committed secrets — the same tool and the same gate the platform's own CI runs. | Any finding blocks the deployment. An unreadable scan also blocks — fail-closed, retried once on a transient failure. |
| **3/9 dependency scan + gate** | pip-audit checks `requirements.txt` against known vulnerabilities — again the same tool and gate as the platform's own CI (`pip-audit --strict`, no severity floor). | Any known-vulnerable pinned dependency blocks. Skipped, not blocked, when there is no `requirements.txt` — most tenants are not Python. |
| **4/9 build** | `docker build` inside a sandboxed container. | A missing `Dockerfile` is detected *before* a build slot is spent. |
| **5/9 push** | Image pushed to the registry, tagged `<team>/<project>-<service>:commit-<sha>`. | Registry credentials travel via a private env-file, never argv. |
| **6/9 image scan + gate** | Trivy scans the pushed image from the registry. | Any CRITICAL or HIGH blocks the deployment. An unreadable or failed scan also blocks — the gate is **fail-closed**. A transient scanner failure is retried once. |
| **7/9 render + apply** | Deployment/Service/Ingress, or an Argo Rollout plus an SLO AnalysisTemplate for canary and blue-green. | Manifests are rendered per project mode, never into another project's workspace. |
| **8/9 rollout** | `kubectl rollout status` with a timeout. | Failure triggers an automatic `rollout undo`. |
| **9/9 live URL** | `http://<service>.<namespace>.<cluster-domain>` recorded on the deployment. | |

The three scanners are exactly what `.github/workflows/ci-cd.yml` runs for the platform's
own services — gitleaks blocks the build, pip-audit gates with `--strict`, Trivy scans the
built image — run automatically on every tenant deploy instead of only on a manually
requested scan (`POST /projects/{id}/scans`). Under Tekton (below) each runs as its own
Task in the tenant's own namespace; on the sandbox path each runs as its own step on the
control-plane host. Same tools, same gates, either path.

Image tags are namespaced per team, so two tenants deploying a service with the same
name cannot overwrite — or be served — each other's image.

---

## Provisioning modes

**Namespace mode** — a slice of the shared cluster. Fast, cheap, and the default.
Each project receives its own namespace with a ResourceQuota, a LimitRange, a
default-deny NetworkPolicy and a dedicated ServiceAccount.

**VM mode** — real virtual machines via Terraform (libvirt/KVM), configured by Ansible
into a Kubernetes cluster. Presets:

| Preset | Nodes | vCPU | Memory | Disk |
| --- | --- | --- | --- | --- |
| `small` | 1 | 2 | 4 GB | 30 GB |
| `medium` | 2 | 4 | 8 GB | 40 GB |
| `large` | 3 | 4 | 8 GB | 50 GB |

A **warm pool** can keep clusters pre-built so a project becomes ready without waiting
for provisioning. Default TTL is 24 h for namespace projects and 4 h for VM projects,
extendable up to 168 h.

---

## Tenancy and access control

Tenancy is a **single boundary — `team_id`** — enforced in the repository layer, so
every router inherits it instead of re-implementing it. A resource belonging to another
team returns **404, not 403**: the platform never confirms that someone else's project
exists.

Namespaces are derived from the **project UUID**, not the project name. Two teams may
both call a project `staging`; they land in different namespaces, and destroying one
cannot touch the other.

Per-team roles, from `core/roles.py`:

| Action | Minimum role |
| --- | --- |
| `project.read` | viewer |
| `project.create`, `project.update` | developer |
| `deployment.create`, `deployment.delete` | developer |
| `scan.create` | developer |
| `project.provision`, `project.extend`, `project.destroy` | owner |
| `team.manage` | admin |

The operator console is gated separately on a **global** `User.role`, set by OIDC group
mapping — deliberately not a per-team membership, since it operates on the
control-plane host itself.

Authentication is local password login or **OIDC**, issuing short-lived JWT access
tokens plus rotating refresh tokens. Login is rate-limited per IP.

---

## Security model

Decisions that are easy to get wrong, and how they are handled here:

- **The vulnerability gate fails closed.** An image whose scan cannot be read is not an
  image known to be safe, so it is refused rather than admitted.
- **Every external command runs sandboxed** — CPU and memory limits, a wall-clock
  timeout, no network unless the command needs it, no host credentials. The Docker
  socket is mounted **only** for image build and push.
- **Secrets never reach a command line.** `docker run -e KEY=value` puts the value in a
  host process's argv, which is world-readable through `/proc`; secrets are written to
  a private (0600) env-file instead and removed after the run.
- **Tenant credentials live in Vault**, never in the database. Git tokens are
  write-only through the API: the platform can use one, nobody can read it back.
- **Private repositories authenticate over HTTPS**, not SSH deploy keys, so the
  repository-URL allowlist (`https` only, known hosts) stays intact. The token reaches
  git through an askpass helper, never embedded in a URL where git would persist it
  into `.git/config`.
- **Job logs are scrubbed** for AWS keys, private keys, bearer tokens, GitHub/GitLab
  tokens and URL-embedded credentials before they are stored.
- **Destroying a project only ever deletes inside `WORKSPACE_ROOT`.** Empty, relative
  or escaping paths are refused and logged.
- **Queries against Prometheus and Loki are built server-side** and pinned to the
  caller's own namespace. No client-supplied PromQL or LogQL is accepted.
- **Every mutating action is audited** with the actor, action, resource and team.

---

## Observability

| Concern | Tool | Notes |
| --- | --- | --- |
| Metrics | Prometheus | scrapes kubelet/cAdvisor via a ServiceMonitor maintained by the Prometheus Operator, so it works on kind, k3s or kubeadm without hardcoded node IPs. `PROMETHEUS_URL` must be reachable *from the control plane*, which on a local cluster means a port-forward |
| Dashboards | Grafana | platform dashboards; anonymous access disabled |
| Logs | Loki, plus ELK with a role, user and Kibana space per team | per-namespace queries built server-side. Loki runs single-binary with filesystem storage in `monitoring`; promtail ships from `logging`, which is exempt from the restricted PodSecurity profile a log collector cannot satisfy |
| Alerts | AlertManager | SLO rules, with history browsable from the operator console |
| Per-project view | the control plane itself | four panels — CPU cores, memory, running pods, container restarts — scoped to the project namespace |

The per-project metrics endpoint tolerates both kube-state-metrics label conventions
(`namespace` and `exported_namespace`), so panels do not silently read empty depending
on scrape configuration.

---

## API reference

All endpoints are under `/api/v1`. 117 endpoints; grouped by router:

| Router | Endpoints |
| --- | --- |
| **auth** | `POST /register`, `POST /login`, `POST /refresh`, `POST /logout`, `GET /me`, `GET /oidc/login`, `GET /oidc/callback` |
| **projects** | `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}`, `POST /projects/{id}/provision`, `/extend`, `/destroy`, `GET /projects/{id}/nodes`, `/plan` |
| **deployments** | `GET/POST /projects/{id}/deployments`, `GET/DELETE /deployments/{id}`, `POST /deployments/{id}/redeploy`, `GET /deployments/{id}/webhook`, `GET /projects/{id}/quota`, `GET /projects/{id}/tekton` |
| **scans** | `GET/POST /projects/{id}/scans`, `GET /scans/{id}`, `GET /scans/{id}/findings`, `GET /projects/{id}/security/summary` |
| **jobs** | `GET /jobs/{id}`, `GET /jobs/{id}/logs`, `POST /jobs/{id}/cancel`, `POST /jobs/{id}/stream-token` |
| **teams** | `GET/POST /teams`, `GET /teams/{id}`, `POST/DELETE /teams/{id}/members`, `GET /teams/{id}/costs`, `GET/PUT/DELETE /teams/{id}/git-credential` |
| **monitoring** | `GET /projects/{id}/metrics` |
| **logs** | `GET /projects/{id}/logs` |
| **catalogue** | `GET /catalogue` |
| **infrastructure** | `GET /infra/capacity`, `/infra/preflight`, `/infra/terraform`, `POST /infra/terraform/reconcile` |
| **webhooks** | `POST /webhooks/{provider}` |
| **platform** (admin) | 62 endpoints: `/overview`, `/health`, `/services`, `/apps`, `/ci`, `/helm`, `/argocd`, `/config`, `/ship/*`, and the `/live/*` family covering pods, logs, alerts, drift, Vault, ArgoCD, CI and scripts |

Job logs stream over a short-lived token so a long-running log stream does not require
sending the access token in a query string.

---

## Data model

| Table | Purpose |
| --- | --- |
| `users`, `refresh_tokens` | accounts, global role, rotating refresh tokens |
| `teams`, `team_members` | tenancy and per-team roles |
| `projects`, `nodes` | environments, their spec, status, TTL and VM node addresses |
| `deployments` | one service: repo URL, branch, port, replicas, strategy, image, live URL |
| `scans`, `findings` | scan runs and individual vulnerabilities/secrets |
| `jobs` | every async operation, with status, streamed log, error and request id |
| `job_steps` | one row per pipeline step of a job — label, status and timing, the source the pipeline graph is drawn from |
| `webhook_subscriptions` | per-deployment push triggers |
| `pooled_clusters` | warm pool of pre-provisioned clusters |
| `audit_log` | who did what, to which resource, in which team |

Schema changes go through Alembic (`controlplane/migrations`).

---

## Background jobs

Celery workers run every long operation; Celery beat runs the periodic ones.

| Task | Trigger |
| --- | --- |
| `provision_task` | project provisioning (Terraform + Ansible, or namespace creation) |
| `deploy_task` | the nine-step pipeline above |
| `undeploy_task` | remove a service's manifests |
| `scan_task` | Trivy, Gitleaks or pip-audit |
| `destroy_task` | tear down namespace or VMs, then the workspace |
| `reap_expired_projects` | every 10 minutes — destroys environments past their TTL |
| `reap_stale_jobs` | unlocks projects whose worker died mid-job |
| `poll_nodes` | every minute — node health |
| `beat_pulse` | every 15 s — liveness of the scheduler itself |
| `replenish_pool` | keeps the warm pool at its configured size |

---

## Repository layout

```text
controlplane/              the platform itself
  api/
    routers/               auth, projects, deployments, jobs, scans, teams, logs,
                           monitoring, catalogue, infrastructure, webhooks, platform
    schemas.py             request/response models
    rbac.py, deps.py       role checks, dependency injection, audit helper
    rate_limit.py          per-IP and per-user limits
  workers/
    tasks.py               provision, deploy, scan, destroy, reap
    celery_app.py          queues and beat schedule
  runners/
    sandbox.py             the container sandbox every command goes through
    tekton.py              opt-in: builds as Tekton PipelineRuns in the tenant's namespace
    gitops.py              publishes rendered manifests to the platform's manifest repo
    terraform_runner.py    init / plan / apply / destroy
    ansible_runner.py      playbook execution
    scanners/              trivy.py, gitleaks.py, pip_audit.py
  renderers/
    namespace.py           namespace, quota, LimitRange, NetworkPolicy, ServiceAccount
    argocd.py              AppProject per team, Application per deployment
    terraform.py, ansible.py
    templates/k8s/         deployment, service, ingress, rollout, analysis
  repositories/            data access — where the tenancy boundary lives
  core/
    config.py              all settings, read from the environment
    security.py            passwords, JWT
    vault.py               secret store (Vault, or a dev fallback)
    git_credentials.py     per-team tokens for private repositories
    app_config.py          per-deployment environment and secrets
    kubeconfigs.py         per-project cluster access
    roles.py               the action → role table
    pipeline_config.py     a repository's own stages, read from .platform.yml
    pipeline_graph.py      the normalised graph contract the console draws
    tekton_status.py       Tekton conditions → the shared status vocabulary
    elk_tenancy.py         per-team Elasticsearch role, user and Kibana space
    redaction.py           log scrubbing
    repo_url.py            repository URL allowlist
    validation.py          namespace derivation, slug rules
    presets.py, pool.py, costs.py, oidc.py, sshkeys.py, runtime.py, logging.py
  parsers/                 scanner output → findings
  models/                  SQLAlchemy models
  migrations/              Alembic
  web/static/              the console (workspace + operator halves)
  tests/                   tenancy, RBAC, isolation, pipeline, security

app/                       the platform's own demo microservices
  users-service/ products-service/ orders-service/ inventory-service/
  catalog/ shared/

k8s/
  apps/base, apps/chart    the demo services' manifests and Helm chart
  gitops/                  the manifest repository ArgoCD syncs tenant workloads from
  tekton/                  opt-in: tenant builds as Pods in the tenant's namespace
  monitoring/              prometheus, grafana, loki, elk, alertmanager,
                           kube-state-metrics, kubelet
  vault/                   Vault deployment and Kubernetes auth
  argocd/                  install and Application definitions
  policies/conftest/       policy tests for manifests

terraform/                 libvirt/KVM VM provisioning
ansible/roles/             docker, k8s_common, k8s_master, k8s_worker, k8s_reset
scripts/                   operational scripts
docs/                      specification, runbooks, SLOs, disaster recovery
tests/                     repository-wide conformance and drift guards
```

---

## Toolchain

| Layer | Tool |
| --- | --- |
| API | FastAPI, Pydantic, SQLAlchemy 2, Alembic |
| Async work | Celery, Redis |
| State | PostgreSQL |
| Infrastructure as code | Terraform (libvirt/KVM — local VMs, no cloud account) |
| Configuration management | Ansible |
| Containers | Docker multi-stage builds, non-root images |
| Orchestration | Kubernetes, Helm, Argo Rollouts |
| GitOps | ArgoCD |
| CI | GitHub Actions with path-filtered, per-service builds |
| Security | Trivy, Gitleaks, pip-audit, HashiCorp Vault, conftest policies |
| Metrics | Prometheus, Grafana, kube-state-metrics |
| Logs | Loki, ELK |
| Alerting | AlertManager |
| Tests | pytest, testcontainers |

---

## Getting started

**Prerequisites** — Docker, a Kubernetes cluster (kind is fine), Python 3.12,
PostgreSQL, Redis. VM mode additionally needs libvirt/KVM.

```bash
# 1. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r controlplane/requirements.txt

# 2. Supporting services the pipeline needs
./scripts/local-registry.sh     # image registry + containerd mirror on the kind nodes
./scripts/local-vault.sh        # secret store (dev mode — read the script's warning)

# 3. Database schema
export DATABASE_URL=postgresql+psycopg://user:pass@localhost/controlplane
# alembic.ini resolves script_location relative to itself, so run it from there
(cd controlplane && alembic upgrade head)

# 4. API and worker (separate shells)
uvicorn controlplane.api.main:app --port 8000
celery -A controlplane.workers.celery_app worker --loglevel=info --concurrency=2
celery -A controlplane.workers.celery_app beat --loglevel=info      # optional: TTL reaping

# 5. A populated tenancy to click around in
python3 scripts/seed-demo.py
```

The console is then at **<http://127.0.0.1:8000>**.

`local-registry.sh` sets up **both halves** of the registry, and both are required: a
registry published on the host so the control plane can push, and a containerd mirror
on every node so the cluster can pull. Inside a node, `localhost:5000` means *that
node*, so a registry the host can reach is otherwise invisible to the kubelet.

### Deploying your first service

1. Log in, create a project, press **Provision** and wait for `ready`.
2. **Deploy an app** with a repository URL (`https://`, GitHub or GitLab), a branch that
   exists, and the port your container listens on.
3. The repository normally needs a **`Dockerfile` at its root**. A plain FastAPI app
   (a `requirements.txt` beside a `main.py`/`app.py` that assigns `app = FastAPI(...)`)
   is detected and built without one.
4. The checkout must be free of committed secrets, `requirements.txt` (if present) free
   of known-vulnerable pinned packages, and the built image free of CRITICAL and HIGH
   vulnerabilities — gitleaks, pip-audit and Trivy each gate the deploy.
5. For a private repository, first store a read-only token under
   **Teams → Private repository access**.

#### Adding your own pipeline stages

The nine built-in stages — clone, secret scan, dependency scan, build, push, image
scan, render, roll out, publish the URL — are the platform's contract, not a
description of what your application needs doing. A repository can declare stages of
its own in **`.platform.yml`** at its root, and they run on the checkout after the two
scan gates and before an image is built, so a failing test stops the pipeline before it
spends a build slot:

```yaml
stages:
  - name: unit tests
    image: python:3.11-slim      # name one, or take the default below —
    run: pip install -r requirements.txt && pytest -q   # neither has your deps
  - name: lint
    image: python:3.11-slim
    run: ruff check .
```

A stage that names no `image` runs in `DEFAULT_STAGE_IMAGE` (`python:3.12-alpine`),
the same on the sandbox path and under Tekton — a `.platform.yml` must not depend on
which of the two an operator has enabled.

Each stage becomes a step in the job's pipeline graph and log, exactly like a built-in
one. Stages run in the same sandbox as everything else: an ephemeral container with CPU,
memory and wall-clock limits and no docker socket.

There is no setting to skip the vulnerability gate — it applies to every build whatever
this file says. A malformed `.platform.yml` fails the deployment with the reason rather
than being ignored, since ignoring it would run a pipeline you did not ask for.

### GitOps for tenant workloads (opt-in)

With `GITOPS_ENABLED=true`, a deploy no longer ends at `kubectl apply`. The worker
commits the manifests it rendered to the platform's own manifest repository
(`k8s/gitops/`) and creates an ArgoCD **Application** that syncs them, so drift is
reconciled continuously instead of accumulating unnoticed.

The isolation rests on two things, because either alone is bypassable:

- every Application's `destination.namespace` is derived from the project UUID, and
- the team's **AppProject** whitelists only the namespaces that team owns, so ArgoCD
  itself refuses a destination outside them — the check that counts, since anyone who
  can edit an Application can edit its destination.

Rendered Secrets are the one manifest GitOps does not manage: a git history keeps them
forever and survives every later rotation, so they are applied directly instead.

Namespace-mode projects only. A VM-mode project runs on its own cluster with no ArgoCD
in it, and the direct apply stays correct there.

### Tekton builds (opt-in)

With `TEKTON_ENABLED=true`, clone, build and scan stop running in a sandbox container on
the control-plane host and become Pods in the **tenant's own namespace** — so they
inherit the ResourceQuota, default-deny NetworkPolicy and ServiceAccount that already
bound the tenant's apps. Builds also stop competing with the API for host CPU.

`docker build` cannot run in a Pod without the node's docker socket, which is root on
the node, so the build step is **kaniko** instead. The vulnerability gate moves into the
scan Task and still fails closed.

A repository's own `.platform.yml` stages become real Tekton tasks, running between the
clone and the build exactly as they do on the sandbox path — so a failing test still
stops the pipeline before it spends a build slot. Dockerfile autogeneration works too:
the platform reads the checkout, generates one, and passes it to the pipeline as a
parameter, because under Tekton the checkout the build sees only exists in the cluster.

The pipeline is therefore built per run rather than referenced, since the tenant's stages
are only knowable after reading their repository. See
[`k8s/tekton/README.md`](k8s/tekton/README.md).

---

## Configuration reference

Everything is read from the environment; see `controlplane/core/config.py`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | — | PostgreSQL connection string |
| `REDIS_URL` | — | Celery broker and rate-limit store |
| `JWT_SECRET`, `JWT_ALGORITHM` | — / `HS256` | access-token signing |
| `ENVIRONMENT`, `DEBUG`, `LOG_FORMAT` | — | runtime mode and log shape |
| `WORKSPACE_ROOT` | `/var/lib/controlplane/workspaces` | per-project workspaces — **the only tree the destroy path may delete** |
| `KUBECONFIG_PATH` | — | cluster used for namespace-mode projects |
| `REGISTRY` | `localhost:5000` | where the control plane pushes images |
| `REGISTRY_INTERNAL` | `kind-registry:5000` | how a sandbox container reaches that registry |
| `REGISTRY_NETWORK` | `kind` | docker network on which that name resolves |
| `REGISTRY_INSECURE` | `true` | allow plain HTTP to a local registry |
| `REGISTRY_USER`, `REGISTRY_PASSWORD` | — | registry credentials |
| `VAULT_ADDR`, `VAULT_TOKEN` | — | secret store; **without it, secrets fall back to a development store that keeps them in plaintext** |
| `VAULT_MOUNT`, `VAULT_KV_VERSION`, `VAULT_SECRETS_PATH` | `secret` / `2` / `controlplane/config` | Vault layout |
| `PROMETHEUS_URL`, `LOKI_URL` | — | metrics and log backends |
| `SANDBOX_CPUS`, `SANDBOX_MEMORY_MB` | — / `1024` | sandbox limits |
| `SANDBOX_NETWORK_ENABLED` | — | allow network inside the sandbox |
| `PROVISION_TIMEOUT_SECONDS`, `SCAN_TIMEOUT_SECONDS` | — | wall-clock ceilings |
| `DEFAULT_TTL_HOURS` | `24` | namespace project lifetime |
| `DEFAULT_VM_TTL_HOURS` | `4` | VM project lifetime |
| `MAX_TTL_HOURS` | `168` | extension ceiling |
| `WARM_POOL_TARGETS` | — | pre-provisioned clusters per preset |
| `LOGIN_RATE_PER_MINUTE` | `5` | per-IP login limit |
| `TEAM_INVITES_PER_HOUR` | `20` | per-user invite limit |
| `AUTH_LOCAL_ENABLED`, `AUTH_OIDC_ENABLED` | — | which login methods are offered |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`, `OIDC_SCOPE`, `OIDC_GROUP_CLAIM` | — | OIDC provider |
| `LIBVIRT_URI`, `STORAGE_POOL`, `NETWORK_INTERFACE`, `DNS_SERVERS` | — | VM mode |
| `TF_STATE_URL`, `TF_STATE_USERNAME`, `TF_STATE_PASSWORD`, `TF_STATE_INSECURE` | — | remote Terraform state |
| `COST_CURRENCY` | — | currency shown in team cost estimates |
| `GITOPS_ENABLED` | `false` | reconcile tenant workloads with ArgoCD instead of applying them once |
| `GITOPS_REPO_URL` | — | the platform's manifest repository, as the **worker** reaches it (NodePort, authenticated HTTP) |
| `GITOPS_REPO_URL_INTERNAL` | falls back to `GITOPS_REPO_URL` | the same repository as **ArgoCD** reaches it (ClusterIP DNS) |
| `GITOPS_BRANCH` | `main` | branch the Applications track |
| `GITOPS_USERNAME`, `GITOPS_PASSWORD` | `controlplane` / — | credentials, if the manifest repository is authenticated |
| `TEKTON_ENABLED` | `false` | run tenant builds as Tekton PipelineRuns in the tenant's namespace |
| `TEKTON_TIMEOUT` | `30m` | pipeline wall-clock ceiling (replaces `SANDBOX_*`, which are docker flags) |
| `REGISTRY_CIDR` | — | the registry as a CIDR, so a build pod may push to it. Required whenever the registry is in RFC1918 space (a local one usually is) — the tenant default-deny egress excludes those ranges, so without it a build resolves the registry and then times out against it |
| `ELASTICSEARCH_URL`, `KIBANA_URL` | — | per-tenant log access; each team gets a role, user and Kibana space |
| `ELASTICSEARCH_USER`, `ELASTICSEARCH_PASSWORD` | `elastic` / — | administrator credential used to provision those |

Secrets (`JWT_SECRET`, `REGISTRY_PASSWORD`, `TF_STATE_PASSWORD`,
`OIDC_CLIENT_SECRET`) may be resolved from Vault instead of the environment; the
control plane refuses to start if one is configured but unreadable.

---

## Scripts

| Script | Purpose |
| --- | --- |
| `seed-demo.py` | rebuild a full demo tenancy through the public API |
| `local-registry.sh` | image registry plus the containerd mirror on every kind node |
| `local-vault.sh` | local Vault for tenant secrets |
| `local-observability.sh` | local Prometheus/Grafana/Loki for development |
| `backup.sh` / `restore.sh` | database and workspaces as one checksummed, same-timestamp unit; restore fails closed |
| `validate-platform.sh` | the platform conformance gate |
| `validate-security.sh` | security checks |
| `smoke-test.sh` | end-to-end smoke test |
| `generate-inventory.sh` | Ansible inventory from Terraform output |
| `render-env.sh` | environment file rendering |
| `bootstrap-vault-secret.sh`, `bootstrap-ghcr-pull.sh`, `bootstrap-elasticsearch-secret.sh` | one-time cluster secret setup |
| `platform-worker-add.sh` / `platform-worker-remove.sh` | grow or shrink the platform's own cluster |
| `stress-hpa.sh`, `stress-panel.py` | load generation for autoscaling demos |
| `hash-requirements.py` | dependency pinning helper |

---

## Testing

```bash
pytest controlplane/tests tests/          # default suite — no cluster required
pytest controlplane/tests tests/ -m ""    # including integration tests
```

The suite covers tenancy and RBAC (a non-member sees 404 everywhere), namespace
isolation (identically named projects in different teams land in different namespaces,
and destroying one never touches the other), the deployment pipeline and its gate,
sandbox behaviour (no secret in argv, output never truncated), credential handling
(never logged, never returned), workspace-deletion safety, renderers, parsers, rate
limits, security headers, and repository-wide drift guards.

Integration tests need Docker; a few need real VMs and SSH.

---

## CI/CD

A single GitHub Actions workflow (`.github/workflows/ci-cd.yml`):

1. **discover** — find which services changed, so untouched services are not rebuilt
2. **test** — the demo services' own tests
3. **controlplane-tests** — the control-plane suite, in its own virtualenv
4. **build** — per-service image build, Trivy scan and push
5. **deploy** — manifest tag updates that ArgoCD then syncs

Path filtering keeps a one-service change from rebuilding everything.

---

## Operations

- **Backups** — `backup.sh` writes the database dump and the Terraform workspaces into
  a single timestamped, checksummed tarball, because restoring a database from 10:00
  next to workspaces from 09:00 can orphan or double-create real infrastructure.
  `restore.sh` verifies the checksum and the manifest stamp, and refuses to overwrite a
  populated workspace root without `FORCE=1`.
- **Runbooks** — [`docs/runbook-index.md`](docs/runbook-index.md), including pod
  crashloops and a sealed Vault.
- **Disaster recovery** — [`docs/disaster-recovery.md`](docs/disaster-recovery.md).
- **SLOs** — [`docs/slo.md`](docs/slo.md), with matching AlertManager rules.

---

## Status and limits

Stated plainly: this is a working platform with real isolation on a shared cluster, not
a hosted product.

- **Several Secrets must be created out-of-band, before first deploy.** Gitea's
  credential (`git-server-admin` + `git-server-repo`), Grafana's admin password, and
  the Elasticsearch/Kibana passwords are deliberately absent from git: an
  ArgoCD-managed Secret with a committed placeholder is reverted to that placeholder
  on every sync, so it is a shared public password rather than a reminder. Each
  manifest carries the `kubectl create secret` line that generates it.
- **Soft multi-tenancy.** Tenants share one cluster, separated by namespaces, quotas
  and network policies. Genuinely untrusted tenants want a cluster each; VM mode is the
  path there, and the per-project kubeconfig plumbing is incomplete.
- **Capacity is the real constraint.** A three-node VM project is roughly 12 GB of RAM,
  so a developer machine fits one or two.
- **Grafana is shared.** Per-tenant views are label-enforced, which is soft isolation —
  fine for a lab, not sufficient for tenants who must never see each other.
- **`local-vault.sh` runs Vault in dev mode**: in-memory, auto-unsealed, a known root
  token. Restarting it discards every stored credential.
  `k8s/vault/manifests.yaml` is the real deployment.
- **Loki keeps logs in an `emptyDir`.** The deployment is single-binary with
  filesystem storage and no PersistentVolumeClaim, so a pod restart discards every
  stored line and retention is capped at 168 h. Fine for a lab; a real deployment
  wants object storage and the read/write/backend split.
- **Kibana tenancy is by index, not by document.** Each team gets an Elasticsearch
  role, user and Kibana space, and Logstash writes one index per tenant namespace
  (`tenant-<namespace>-*`). That is what the basic licence can enforce; filtering rows
  inside a shared index needs document-level security, which is Platinum. A log line
  whose namespace field is missing or malformed is routed to the platform index, where
  only operators can read it, rather than being allowed to invent an index name.

## License

MIT — see [LICENSE](LICENSE).
