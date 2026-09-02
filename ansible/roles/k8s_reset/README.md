# k8s_reset

Destructive teardown of a kubeadm cluster: `kubeadm reset`, removal of Kubernetes/CNI/etcd
state, iptables/ipvs flush, and service stop. Opt-in only — `../../playbook.yml` applies
this role with `when: reset_confirmed | default(false) | bool` and tags it
`[reset, never]`, so it requires both `--tags reset` and `-e reset_confirmed=true` on the
command line; a plain `ansible-playbook playbook.yml` never touches it.

- `tasks/main.yml` — interactive `pause` confirmation listing exactly what will be
  destroyed, then `kubeadm reset -f`; removes `/etc/kubernetes`, `/etc/cni`,
  `/var/lib/etcd`, `/var/lib/kubelet`, `/var/lib/cni`, `/var/lib/dockershim`,
  `/var/run/kubernetes`, `/opt/cni/bin`, and the `devops` user's `~/.kube`; flushes
  iptables (filter/nat/mangle tables + custom chains) and ipvsadm rules; deletes the
  Calico `tunl0` interface; stops `kubelet`/`containerd`; removes
  `/etc/cni/net.d`/`/var/lib/calico`. All destructive commands are tagged `reset` and most
  use `ignore_errors: true` so a partial/already-reset node doesn't abort the run.
- `defaults/main.yml` — `k8s_reset_force: false` (unused toggle reserved for future force
  behavior; the real gate is `reset_confirmed` in the top-level playbook).
- `meta/main.yml` — Galaxy metadata.
