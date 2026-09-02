# tasks

The task list for the `k8s_common` role.

- `main.yml` — disables swap (`swapoff -a` + comments out fstab swap lines); creates
  `/etc/modules-load.d`, loads `overlay`/`br_netfilter` via `community.general.modprobe`,
  and persists them; writes and applies the kubeadm-required sysctl params
  (`net.bridge.bridge-nf-call-iptables`/`ip6tables`, `net.ipv4.ip_forward`) via
  `sysctl --system`; adds the Kubernetes apt repo (`pkgs.k8s.io`) with a dearmored GPG key;
  installs `kubelet`/`kubeadm`/`kubectl` pinned to `k8s_version_deb` with
  `allow_downgrade: true`; holds those packages via `dpkg_selections` so unattended-upgrades
  can't bump them past the pinned version; enables the `kubelet` service. Every task is
  tagged `k8s`.
