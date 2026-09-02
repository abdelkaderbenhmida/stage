# k8s_master

Bootstraps the Kubernetes control plane on `[masters]` hosts: `kubeadm init`, kubeconfig
setup, Calico CNI, and generating/staging the worker join command. Also fetches a
project-scoped kubeconfig back to the control-plane workspace for VM-mode projects. No
Molecule scenario — this role needs a real multi-node cluster to test.

- `tasks/main.yml` — runs `kubeadm init` (advertise address, pod/service CIDR, CRI
  socket, `--upload-certs`, skipping the coredns/kube-proxy addon phases so they can be
  installed with retries afterward), copies `/etc/kubernetes/admin.conf` to the `devops`
  user's `~/.kube/config`, waits for the API server, installs the kube-proxy and coredns
  addons, installs Calico via the Tigera operator (server-side apply to dodge the
  last-applied-config annotation size limit) plus the Calico custom-resources manifest,
  waits for all nodes Ready (fails closed — a broken CNI aborts the play), generates the
  `kubeadm token create --print-join-command` output (`no_log: true` — it's a cluster
  bootstrap secret), stages it as `/tmp/kubeadm-join.sh` (0700) and
  `/tmp/kubeadm-join.txt` (0600) for `k8s_worker` to fetch, then rewrites the admin
  kubeconfig's server address to this master's advertised IP and fetches it back to
  `project_kubeconfig_dest` (default `./kubeconfig.yaml`) before deleting the staged copy.
- `defaults/main.yml` — `calico_version`, Tigera operator/custom-resources manifest URLs
  derived from it, and the join-command file paths/mode (`kubeadm_join_mode: "0600"`).
- `meta/main.yml` — Galaxy metadata.
