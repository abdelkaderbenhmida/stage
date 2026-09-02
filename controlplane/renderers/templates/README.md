# renderers/templates

Jinja2/template sources rendered by `controlplane/renderers/`. Nothing here is loaded
directly by routers or workers — always through the matching renderer module, which
supplies the validated context.

- `terraform/` — VM-mode: cloud-init, network-config, and the Terraform workspace
  (`main.tf`, `variables.tf`, `outputs.tf`) used when a project is *not* namespace-mode.
- `ansible/` — VM-mode: inventory and group_vars consumed after Terraform provisions the
  nodes, to install Kubernetes on them.
- `k8s/` — namespace-mode and deployment manifests: Deployment, Service, Ingress,
  Secret, and the Argo Rollouts `rollout.yaml.j2` / `analysis.yaml.j2` pair used for
  canary/progressive delivery. These are the manifests `runners/gitops.py` commits to
  the platform manifest repo when `GITOPS_ENABLED`, or applies directly with `kubectl`
  otherwise. There is no separate Tekton template set here — `TEKTON_ENABLED` submits a
  `PipelineRun` object built in `runners/tekton.py` against the cluster's own
  `k8s/tekton/pipeline.yaml`, not a Jinja template.
