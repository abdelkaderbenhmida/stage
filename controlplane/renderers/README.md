# renderers

Turn a validated `InfraSpec` (or a deployment) into the artifacts the runners actually
apply: Terraform/Ansible for VM-mode, Kubernetes manifests for namespace-mode, and
ArgoCD objects when GitOps is enabled. Renderers are pure — no cluster or shell access —
so they're unit-testable without Docker.

- `terraform.py` — InfraSpec → a self-contained Terraform workspace (`main.tf`
  generalizes to one master + `worker_count` workers).
- `ansible.py` — InfraSpec → Ansible inventory + group_vars; groups derive from node
  roles (`masters`, `workers`, ...).
- `namespace.py` — namespace-mode rendering: a namespace with ResourceQuota, LimitRange,
  default-deny NetworkPolicy and a scoped ServiceAccount. Node sizing from the spec is
  reinterpreted as the total budget for the namespace. Quota and NetworkPolicy are load
  bearing, not optional — without them a namespace is a naming convention, not isolation.
- `argocd.py` — one ArgoCD AppProject per team, one Application per deployment. Used
  only when `GITOPS_ENABLED`. Isolation depends on **both** the Application's
  `destination.namespace` (derived server-side from the project UUID, never user input)
  and the team's AppProject whitelist — the AppProject is the server-side half of the
  tenancy boundary and is not optional.
- `templates/` — Jinja2 templates consumed by the renderers above, split by target
  (`terraform/`, `ansible/`, `k8s/`); see its README for the GitOps/Tekton/VM-mode split.
