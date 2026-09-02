# defaults

Default variables for the `k8s_worker` role.

- `main.yml` — only `cri_endpoint` (the containerd CRI socket). Nothing else is needed
  here: the kubeadm join command itself, fetched from the master, carries the pod/service
  CIDR and token information.
