# defaults

Default variables for the `k8s_common` role (used standalone; `group_vars/all.yml`
overrides these in the full playbook run).

- `main.yml` — `k8s_version`/`k8s_version_deb` (kubeadm package pin), `pod_cidr`,
  `service_cidr`, containerd socket/CRI endpoint paths, `k8s_kernel_modules`
  (`br_netfilter`, `overlay`), and `k8s_sysctl_params` (bridge-nf-call-iptables/ip6tables,
  ip_forward) — the exact set of sysctls kubeadm's preflight checks require.
