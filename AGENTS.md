# DevOps Central Platform — AGENTS.md

## Structure

2 active modules; rest are planned but not yet created:

```
terraform/          # KVM VMs via libvirt (exists, applied)
ansible/            # Docker + K8s 1.28 on Ubuntu 22.04 (exists, partially working)
app/                # 3 FastAPI microservices (planned — NOT created)
k8s/                # K8s manifests (planned — NOT created)
.github/workflows/  # CI/CD pipeline (planned — NOT created)
files.md/           # Documentation + working notes
```

No git repo. No test/lint/CI config exists.

## Terraform

- Provider: `dmacvicar/libvirt` v0.9, local KVM only (no cloud)
- Backend: local (single-dev, no remote state locking)
- 3 VMs: `master-01` (192.168.56.10) + 2 workers (192.168.56.11-12)
- SSH user: `devops`, key auto-detected from `~/.ssh/id_ed25519.pub` (falls back to `id_rsa.pub`)
- Host: Ubuntu 24.04, VMs: Ubuntu 22.04 cloud image
- Network: NAT mode `192.168.56.0/24`, DNS via 1.1.1.1 / 8.8.8.8
- **Known libvirt bug**: `create.content.url` ignores `capacity`. Get 20G root by: (1) `virsh vol-resize <vol> 20G` after `terraform apply`, (2) cloud-init `growpart` + `resizefs` (already in `cloud-init.tpl`)
- Generates `terraform/inventory.ini` for Ansible via `inventory.tpl`
- Variables configurable in `variables.tf` (disk size, vCPU, memory, etc.)

### Commands

```bash
terraform init
terraform plan
terraform apply       # provisions VMs
terraform output      # shows IPs + inventory content
```

## Ansible

- **All playbook tags have `never` set** — must invoke explicitly:
  ```bash
  ansible-playbook playbook.yml --tags docker,k8s,master,worker
  ansible-playbook playbook.yml --tags reset   # kubeadm reset -f on all nodes
  ```
- **Play order matters**: `docker` → `k8s_common` → `k8s_master` → `k8s_worker`
- `docker` role also configures **containerd** with `SystemdCgroup=true`
- CRI = containerd (not Docker), even though Docker CE is installed
- `k8s_master` has `creates` guard on `/etc/kubernetes/admin.conf` — won't re-init
- Use `serial: 1` for workers (join one at a time)
- Role structure: `docker/`, `k8s_common/`, `k8s_master/`, `k8s_worker/`, `k8s_reset/`
- Calico v3.26.1, Pod CIDR `192.168.0.0/16`, Service CIDR `10.96.0.0/12`
- k8s packages pinned to 1.28.\*, apt-held to prevent upgrades
- `k8s_reset` role is opt-in (tagged `reset`) — confirms before destroying cluster
- `ansible.cfg`: `host_key_checking=False`, `pipelining=True`, user `devops`, key `~/.ssh/id_ed25519`

## Current State

**Cluster broken**: API server crash-looping, `kubectl` returns connection refused to `192.168.56.10:6443`. API server logs show etcd connection issues. Known next move: diagnose kube-apiserver pod logs, check etcdctl endpoint health, or `--tags reset` and re-init.

## files.md/ Directory

Contains project descriptions, implementation guide, task lists, architecture notes. Read-only reference material. Not executable code.

## Planned (not yet implemented)

- `app/` — 3 FastAPI microservices with Dockerfiles
- `k8s/` — Deployments, Services, HPA, RBAC + monitoring (Prometheus/Grafana/ELK), Vault, ArgoCD, Flagger/Canary manifests
- `.github/workflows/` — CI/CD: lint → Gitleaks → tests → build → Trivy → push
- `scripts/validate-platform.sh` — end-to-end validation
