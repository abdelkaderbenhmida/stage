# renderers/templates/ansible/group_vars

Ansible `group_vars` rendered from the InfraSpec's `config` block.

- `all.yml.j2` — Kubernetes version, CNI plugin (+ optional Calico version), pod/service
  CIDRs, containerd socket, CRI endpoint, and Docker version. Only enum-constrained
  values from `InfraSpec.config` are ever substituted here — free-form input never
  reaches an Ansible variable file.
