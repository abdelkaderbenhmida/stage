# terraform

Provisions the platform's Kubernetes cluster as local libvirt/KVM VMs (one master,
`worker_count` workers) on a private NAT network, then renders the Ansible inventory
Ansible needs to configure them. There is no cloud account and state is a local file
(`backend.tf`) — the control plane serializes Terraform jobs per project instead of
relying on backend locking. `terraform.tfstate` and `terraform.tfvars` are gitignored
since the state file carries the SSH public key in cleartext.

- `backend.tf` — local state backend; explains why (no cloud, single-operator homelab)
  and how the control plane keeps concurrent applies safe without native locking.
- `main.tf` — provider config, master/worker node topology (derived from
  `network_cidr` and `worker_count`), and the `libvirt_network`/VM resources.
- `variables.tf` — all input variables (VM specs, network CIDR, SSH user, image path,
  etc.) with descriptions and defaults.
- `outputs.tf` — master/worker/node IPs and the rendered Ansible inventory path and
  content; all marked `sensitive` so `terraform plan` doesn't echo IPs.
- `cloud-init.tpl` — per-VM cloud-init: creates the SSH user with restricted sudo
  (kubeadm/kubelet/containerd only), disables root login and password auth, installs
  fail2ban, enables disk growpart/resizefs.
- `network-config.tpl` — netplan template for static IP, gateway, and DNS on each node.
- `inventory.tpl` — Ansible inventory template (`[masters]`/`[workers]` groups) filled
  in from the Terraform node list.
- `terraform.tfvars.example` — example variable values to copy to `terraform.tfvars`
  (gitignored) and edit for the local host.
- `.terraform.lock.hcl` — provider version lock file.

`.terraform/` is a local provider plugin cache and is not covered by this README.
