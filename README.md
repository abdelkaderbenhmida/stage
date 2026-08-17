# DevOps Central Platform

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A **self-service deployment platform**. A user signs up, creates a project, points it
at a Git repository, and the platform builds the code, scans the image for
vulnerabilities, refuses it if the scan fails, deploys it to an isolated Kubernetes
namespace, and gives back a URL — with metrics, logs and a lifetime after which the
environment is destroyed.

Several tenants share one cluster and cannot see, reach, or delete each other's
work.

> The specification documents in [`docs/`](docs/) are written in French:
> [`DevOps_Central_Platform_Description.md`](docs/DevOps_Central_Platform_Description.md)
> and [`DevOps_Central_Platform_Etapes_Implementation.md`](docs/DevOps_Central_Platform_Etapes_Implementation.md).

---

## Where each user's code lives

This is the question the design turns on, so it is worth answering first: **the
platform stores no tenant source code at all.**

A deployment holds a *pointer*, not a copy:

```
repo_url  = https://github.com/some-org/their-service.git
branch     = main
image_ref  = registry/<team>/<project>-<service>:commit-abc1234
```

At deploy time the worker shallow-clones the repository into a throwaway directory,
builds an image, scans it, pushes it to the registry, **removes `.git`**, and then
deletes the clone. What persists is the *image*. The source stays where its owner
keeps it.

Three things are easy to confuse:

| | Holds | Where |
|---|---|---|
| **Tenant code** | a user's application source | their own Git repository (external) |
| **Workspaces** | Terraform state, generated inventories | `WORKSPACE_ROOT`, one per project |
| **`app/`** | the platform's *own* demo microservices | this repository — **not** tenant code |

That last row is the common misreading: `app/users-service` and friends belong to the
platform and are managed from the operator console. No tenant's code ever lands there.

---

## What it does

**For a tenant**

- Create a **project** — a quota-bounded namespace on the shared cluster, or a set of
  real VMs provisioned with Terraform and Ansible.
- **Deploy a service** from any public repository (or a private one, with a token the
  team supplies). Build → scan → push → rollout, with a live log of every step.
- **Scan** a repository or image on demand: Trivy for images, Gitleaks for secrets,
  pip-audit for dependencies.
- Read **metrics** and **logs** for their own namespace, with the queries built
  server-side so nobody can widen them to another tenant.
- Environments carry a **TTL** and are reaped automatically when it expires.

**For an operator**

A separate console (`Operations`, admin-only) covering the platform's own services:
health, deployments, ArgoCD, Vault, Terraform reconciliation, and the CI matrix.

## How a deployment runs

```
[1/7] clone repository        shallow clone of the requested branch, no credentials
                              unless the team configured one; .git is removed after
[2/7] build image             docker build inside a sandboxed container
[3/7] push to registry        image tagged per team, project, service and commit
[4/7] Trivy scan + gate       any CRITICAL or HIGH blocks the deployment;
                              an unreadable scan also blocks (fail-closed)
[5/7] render + apply          Deployment/Service/Ingress, or an Argo Rollout for
                              canary and blue-green strategies
[6/7] wait for rollout        failure triggers an automatic rollback
[7/7] capture the live URL
```

## Isolation

Tenancy is a single boundary — `team_id` — enforced in the repository layer, so every
router inherits it rather than re-implementing it. A resource belonging to another team
returns **404, not 403**: the platform never confirms that someone else's project
exists.

Each project gets its own namespace, derived from the project UUID rather than its
name. Two teams may both call a project `staging`; they still land in different
namespaces, and destroying one cannot touch the other. Every namespace carries a
ResourceQuota, a LimitRange, a default-deny NetworkPolicy and its own ServiceAccount.

Security-relevant behaviour that is easy to get wrong, and how it is handled here:

- **The vulnerability gate fails closed.** An image whose scan cannot be read is not
  an image known to be safe, so it is refused rather than admitted.
- **Commands run sandboxed** — CPU and memory limits, no network unless required, no
  host credentials. The Docker socket is mounted only for image build and push.
- **Secrets never reach a command line.** `docker run -e` puts values in a host
  process's argv, which is world-readable; secrets go through a private env-file.
- **Tenant credentials live in Vault**, never in the database, and no endpoint can
  read them back.
- **Job logs are scrubbed** before they are stored.

## Stack

| Layer | Tool |
|---|---|
| Control plane | FastAPI, SQLAlchemy, Alembic, Celery, PostgreSQL, Redis |
| Infrastructure as code | Terraform (libvirt/KVM — local VMs, no cloud account) |
| Configuration | Ansible (`docker`, `k8s_common`, `k8s_master`, `k8s_worker`) |
| Orchestration | Kubernetes, Helm, Argo Rollouts |
| Security | Trivy, Gitleaks, pip-audit, HashiCorp Vault |
| GitOps | GitHub Actions, ArgoCD |
| Observability | Prometheus, Grafana, Loki/ELK, AlertManager |

## Repository layout

```
controlplane/        the platform itself
  api/routers/       auth, projects, deployments, jobs, scans, teams,
                     logs, monitoring, catalogue, infrastructure, webhooks, platform
  workers/           Celery tasks: provision, deploy, scan, destroy, reap
  runners/           sandboxed execution: terraform, ansible, kubectl, scanners
  renderers/         Kubernetes, Terraform and Ansible manifest rendering
  repositories/      data access, where the tenancy boundary is enforced
  core/              config, security, Vault, validation, redaction
  web/static/        the console (tenant workspace + operator console)
  migrations/        Alembic
  tests/             tenancy, RBAC, isolation, pipeline, security
app/                 the platform's own demo microservices
k8s/                 manifests: apps, monitoring, Vault, ArgoCD
terraform/ ansible/  VM-mode provisioning
scripts/             operational scripts (see below)
tests/               repository-wide conformance and drift guards
```

## Getting started

Requires Docker, a Kubernetes cluster (kind is fine), Python 3.12, PostgreSQL and Redis.

```bash
# 1. Supporting services the pipeline needs
./scripts/local-registry.sh     # image registry + containerd mirror on the kind nodes
./scripts/local-vault.sh        # secret store (dev mode; see the script's warning)

# 2. Database schema
alembic -c controlplane/alembic.ini upgrade head

# 3. API and worker
uvicorn controlplane.api.main:app --port 8000
celery -A controlplane.workers.celery_app worker --loglevel=info --concurrency=2

# 4. A populated tenancy to click around in
python3 scripts/seed-demo.py
```

The console is then at **http://127.0.0.1:8000**.

`local-registry.sh` sets up both halves of the registry, and both are needed: a
registry published on the host so the control plane can push, and a containerd mirror
on every node so the cluster can pull. Inside a node, `localhost:5000` means *that
node*, so a registry the host can reach is still invisible to the kubelet.

### Useful scripts

| Script | Purpose |
|---|---|
| `seed-demo.py` | rebuild a demo tenancy through the public API |
| `local-registry.sh` / `local-vault.sh` | stand up the registry and secret store |
| `backup.sh` / `restore.sh` | database + workspaces as one checksummed unit |
| `validate-platform.sh` | the platform conformance gate |
| `validate-security.sh` | security checks |
| `smoke-test.sh` | end-to-end smoke test |

## Tests

```bash
pytest controlplane/tests tests/          # default suite
pytest controlplane/tests tests/ -m ""    # including integration tests
```

The default suite runs without a cluster. The integration tests need Docker, and some
need real VMs and SSH.

## Configuration

Read from the environment (see `controlplane/core/config.py`):

| Variable | Meaning |
|---|---|
| `DATABASE_URL`, `REDIS_URL` | control-plane storage |
| `WORKSPACE_ROOT` | per-project workspaces — **the only tree the destroy path may delete** |
| `REGISTRY`, `REGISTRY_INTERNAL`, `REGISTRY_NETWORK` | how the platform and the sandbox each reach the registry |
| `VAULT_ADDR`, `VAULT_TOKEN` | secret store; without it secrets fall back to a development store that keeps them in plaintext |
| `KUBECONFIG_PATH` | cluster used for namespace-mode projects |

## Status and limits

Honest about what this is: a working platform with real isolation on a shared cluster,
not a hosted product.

- **Soft multi-tenancy.** Tenants share one cluster, separated by namespaces, quotas
  and network policies. Genuinely untrusted tenants want a cluster each; the VM mode
  is the path there, and the kubeconfig plumbing for it is incomplete.
- **Capacity is the real constraint.** A three-node VM project is roughly 12 GB of
  RAM, so a developer machine fits one or two.
- **Grafana is shared.** Per-tenant views are label-enforced, which is soft isolation —
  fine for a lab, not sufficient for paying customers.
- **`local-vault.sh` runs Vault in dev mode**: in-memory, auto-unsealed, a known root
  token. Restarting it discards every stored credential. `k8s/vault/manifests.yaml` is
  the real deployment.

## License

MIT — see [LICENSE](LICENSE).
