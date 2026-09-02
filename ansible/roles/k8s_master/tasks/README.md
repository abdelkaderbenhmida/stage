# tasks

The task list for the `k8s_master` role. See `../README.md` for the full sequence
(kubeadm init, kubeconfig, addons, Calico, join-command generation, project kubeconfig
fetch).

- `main.yml` — all master-bootstrap logic in one file, tagged `master`. Notable
  safety details inline: `no_log: true` on every task that touches the kubeadm bootstrap
  token or the fetched kubeconfig content; `ignore_errors: false` on the Calico
  readiness wait ("fail-closed: broken Calico ⇒ broken cluster, abort"); a `TODO` noting
  the Tigera manifest download has no checksum verification yet.
