# k8s_common

Kubeadm prerequisites shared by both masters and workers: disables swap, loads the kernel
modules and sysctl params kubeadm requires, and installs the pinned
kubelet/kubeadm/kubectl packages. Runs on every host after the `docker` role and before
`k8s_master`/`k8s_worker`.

- `tasks/main.yml` — `swapoff -a` + comments out swap entries in `/etc/fstab`; loads
  `overlay`/`br_netfilter` via `community.general.modprobe` and persists them in
  `/etc/modules-load.d/k8s.conf`; writes bridge/forwarding sysctls to
  `/etc/sysctl.d/99-k8s.conf` and applies them with `sysctl --system`; adds the Kubernetes
  apt repo (GPG key dearmored to `kubernetes-apt-keyring.gpg`); installs
  `kubelet`/`kubeadm`/`kubectl` pinned to `k8s_version_deb`, holds them via
  `dpkg_selections` to block auto-upgrade, and enables the `kubelet` service.
- `defaults/main.yml` — `k8s_version`, `k8s_version_deb`, `pod_cidr`, `service_cidr`,
  containerd socket/CRI endpoint, kernel module list, and sysctl param list — role-local
  copies of the same keys set in `../../group_vars/all.yml`.
- `meta/main.yml` — Galaxy metadata.
- `molecule/` — Molecule test scenario for this role.

Note: `community.general.modprobe` requires the `community.general` collection declared
in `../../requirements.yml` — install it before running, or this role fails on a fresh
workstation.
