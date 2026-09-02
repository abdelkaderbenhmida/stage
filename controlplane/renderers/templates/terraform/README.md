# renderers/templates/terraform

VM-mode infrastructure templates, rendered by `controlplane/renderers/terraform.py` and
applied by `runners/terraform_runner.py`. Targets libvirt/KVM.

- `main.tf.j2` — the workspace: one master plus `worker_count` workers, generalized from
  the InfraSpec node list.
- `variables.tf.j2` — input variables (libvirt URI, storage pool, etc.).
- `outputs.tf.j2` — node IPs and roles, both marked `sensitive`.
- `cloud-init.tpl` — per-node cloud-init: fail-fast security defaults (`ssh_pwauth:
  false`, `disable_root: true`).
- `network-config.tpl` — static network config (address, gateway, nameservers) for a node.
