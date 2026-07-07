# Ansible in this project — what it does and how

## Goal
Automate setup of a Kubernetes cluster (1 master + 2 workers) on the KVM VMs that Terraform provisioned. Installs Docker/containerd, k8s binaries, bootstraps the control plane, joins workers, and optionally tears it all down.

## Target hosts (`inventory.ini`)
- `master-01` → `192.168.56.10`
- `worker-01` → `192.168.56.11`
- `worker-02` → `192.168.56.12`
- SSH user: `devops`, key `~/.ssh/id_ed25519`, host key checking off.

## Playbook (`playbook.yml`) — 5 plays, strict order

| # | Play                     | Hosts               | Role         | Tag      |
|---|--------------------------|---------------------|--------------|----------|
| 1 | Docker + containerd      | all                 | `docker`     | `docker` |
| 2 | K8s common prereqs       | all                 | `k8s_common` | `k8s`    |
| 3 | Bootstrap master         | masters             | `k8s_master` | `master` |
| 4 | Join workers             | workers (serial: 1) | `k8s_worker` | `worker` |
| 5 | Destroy cluster (opt-in) | all                 | `k8s_reset`  | `reset`  |

All tags have `never` set globally — you must invoke explicitly:
```bash
ansible-playbook playbook.yml --tags docker,k8s,master,worker
```

## The 5 roles, step by step

### 1. `docker/tasks/main.yml` (run on all 3 nodes)
1. Install `ca-certificates curl gnupg lsb-release`
2. Add Docker's official apt repo + GPG key (`download.docker.com`)
3. Install `docker-ce`, `docker-ce-cli`, `containerd.io`
4. Start + enable `docker` service; add `devops` user to `docker` group
5. Generate `/etc/containerd/config.toml` via `containerd config default`
6. Flip `SystemdCgroup = true` (required by k8s kubelet) → notify handler restart containerd
7. Enable + start `containerd`

> Key point: CRI used by k8s = **containerd**, not Docker. Docker is installed for convenience/tooling only.

### 2. `k8s_common/tasks/main.yml` (run on all 3 nodes)
1. `swapoff -a` + comment swap lines in `/etc/fstab` (k8s refuses to start with swap on)
2. Load kernel modules `overlay` + `br_netfilter`, persist via `/etc/modules-load.d/k8s.conf`
3. Write sysctl params: `bridge-nf-call-iptables=1`, `ip6tables=1`, `ip_forward=1` → `sysctl --system`
4. Add Kubernetes apt repo (`pkgs.k8s.io`, pinned to v`1.28`)
5. Install `kubelet`, `kubeadm`, `kubectl` version `1.28.*` with `allow_downgrade`
6. `dpkg --set-selections hold` on all 3 packages → prevent auto-upgrade
7. Enable `kubelet` service (not started yet — kubeadm starts it)

### 3. `k8s_master/tasks/main.yml` (run on `master-01` only)
1. `kubeadm init` with:
   - `--apiserver-advertise-address=192.168.56.10`
   - `--pod-network-cidr=192.168.0.0/16` (Calico)
   - `--service-cidr=10.96.0.0/12`
   - `--cri-socket=unix:///run/containerd/containerd.sock`
   - `--upload-certs`
   - `--skip-phases=addon/coredns,addon/kube-proxy` (added manually later)
   - Guard: `creates: /etc/kubernetes/admin.conf` → idempotent, won't re-init
2. Copy `/etc/kubernetes/admin.conf` → `/home/devops/.kube/config` (chmod 0600)
3. Poll `kubectl version` with retries until API server responds
4. Install `kube-proxy` addon (`kubeadm init phase addon kube-proxy`)
5. Install `coredns` addon (`kubeadm init phase addon coredns`)
6. Download + apply **Calico Tigera operator** manifest (server-side apply to dodge annotation limit)
7. Wait for `tigera-operator` deployment to be Available
8. Apply Calico `custom-resources.yaml`
9. Wait for all nodes Ready (only master at this point)
10. Generate join command via `kubeadm token create --print-join-command`
11. Save the join command to `/tmp/kubeadm-join.txt` on master for workers to fetch

### 4. `k8s_worker/tasks/main.yml` (run on workers, **serial: 1** — one at a time)
1. `slurp` `/tmp/kubeadm-join.txt` from master (delegates to master)
2. Decode base64 + assert it contains `kubeadm join`
3. Run the join command; guarded by `creates: /etc/kubernetes/kubelet.conf` → idempotent
4. Restart `kubelet` on worker
5. Poll `kubectl get node <worker>` (delegated to master) until status == `Ready` (24 retries × 10s)

### 5. `k8s_reset/tasks/main.yml` — **opt-in only** (`--tags reset`)
1. `pause` for interactive confirmation (Ctrl+C to abort)
2. `kubeadm reset -f` on all nodes
3. Wipe `/etc/kubernetes`, `/etc/cni`, `/var/lib/etcd`, `/var/lib/kubelet`, `/var/lib/cni`, `/opt/cni/bin`, etc.
4. Remove devops `~/.kube`
5. Flush `iptables` (filter/nat/mangle + delete custom chains)
6. `ipvsadm --clear` if installed
7. Delete `tunl0` Calico interface
8. Stop `kubelet` + `containerd` services
9. Remove `/etc/cni/net.d` + `/var/lib/calico`

## How it runs end-to-end
```bash
# 1. Terraform already created the VMs + ansible/inventory.ini
cd ansible

# 2. Fresh cluster bootstrap
ansible-playbook playbook.yml --tags docker,k8s,master,worker
#    → docker role       : containerd + Docker on 3 nodes
#    → k8s_common role   : swap off, modules, sysctl, kubelet/kubeadm/kubectl 1.28
#    → k8s_master role    : kubeadm init, Calico, join token
#    → k8s_worker role    : workers join one-by-one, wait for Ready

# 3. Nuke cluster and start over
ansible-playbook playbook.yml --tags reset

# 4. Re-bootstrap after reset
ansible-playbook playbook.yml --tags docker,k8s,master,worker
```

## Idempotency guards
- `docker`: GPG key dearmor uses `creates:`; services use `state: started`
- `k8s_master`: `kubeadm init` has `creates: /etc/kubernetes/admin.conf` → safe to re-run, won't wipe cluster
- `k8s_worker`: join has `creates: /etc/kubernetes/kubelet.conf` → won't re-join a node already in cluster

## Current status (per AGENTS.md)
**Cluster broken.** API server crash-looping, `kubectl` → connection refused to `192.168.56.10:6443`. Logs show etcd connection issues. Recommended next debug steps:
- `kubectl logs -n kube-system kube-apiserver-master-01` (or via `crictl logs` if API down)
- `crictl --runtime-endpoint unix:///run/containerd/containerd.sock ps` to see pod states directly
- `sudo ETCDCTL_API=3 etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key endpoint health`
- If unfixable: `ansible-playbook playbook.yml --tags reset` then re-init.

## Config centralization (`group_vars/all.yml`)
```yaml
k8s_version: "1.28"
calico_version: "v3.26.1"
pod_cidr: "192.168.0.0/16"      # Calico
service_cidr: "10.96.0.0/12"
containerd_socket: "/run/containerd/containerd.sock"
cri_endpoint: "unix:///run/containerd/containerd.sock"
```

That's the full picture: Ansible takes raw VMs from Terraform and turns them into a working K8s 1.28 cluster with Calico CNI, idempotently, with a built-in reset path.
