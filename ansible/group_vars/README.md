# group_vars

Ansible group variables applied to every host in the inventory (the `all` group).

- `all.yml` — cluster-wide defaults consumed by the `k8s_common` and `k8s_master` roles:
  `k8s_version`/`k8s_version_deb` (kubelet/kubeadm/kubectl package pin), `calico_version`,
  `pod_cidr`, `service_cidr`, and the containerd socket/CRI endpoint paths. These are the
  single source of truth the roles fall back to; role-level `defaults/main.yml` files
  duplicate the same keys with lower precedence for standalone role use.
