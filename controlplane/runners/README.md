# runners

Everything that actually shells out or talks to the cluster/registry, on behalf of the
Celery tasks in `workers/tasks.py`. **Every external command goes through
`sandbox.py`** — nothing shells out directly. Secrets go into a 0600 env-file, never
argv, because `docker run -e K=V` is readable by any local process via `/proc`.

- `sandbox.py` — container-isolated command execution; the one place a subprocess is
  actually launched. The Docker socket is mounted only for build and push.
- `terraform_runner.py` — Terraform apply/destroy on the sandbox; per-project state is
  stored inside the rendered workspace so each project gets its own state file.
- `ansible_runner.py` — Ansible playbook runs on the sandbox; mounts the repo's own
  `ansible/` (roles + playbook) read-only.
- `gitops.py` — publishes rendered tenant manifests into the platform's manifest repo
  for ArgoCD to sync from (`GITOPS_ENABLED` path); ArgoCD only syncs from git, so the
  platform can't hand it manifests directly.
- `tekton.py` — submits a tenant's build as a Tekton `PipelineRun` in the tenant's own
  namespace, built with kaniko (`TEKTON_ENABLED` path). A repo's own `.platform.yml`
  stages each become their own Task in the submitted run; Dockerfile autogeneration is
  lost on this path since it needs a host checkout this path never produces.
- `scanners/` — the security scan tool runners (Trivy, Gitleaks, pip-audit); see its README.
