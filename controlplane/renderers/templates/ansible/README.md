# renderers/templates/ansible

Ansible inventory template for VM-mode provisioning, rendered by
`controlplane/renderers/ansible.py` after Terraform has created the nodes.

- `inventory.ini.j2` — groups nodes by role (`masters`, `workers`, ...) with each node's
  IP and the SSH user to connect as.
- `group_vars/` — per-run variable files rendered alongside the inventory; see its README.
