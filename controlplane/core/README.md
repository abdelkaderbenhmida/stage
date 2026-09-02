# core

Cross-cutting business logic and infrastructure glue that isn't tied to a single HTTP
route: settings, security primitives, per-tenant secret and credential handling, and the
small translation layers that keep external system quirks (Tekton conditions, pipeline
YAML) out of the routers and workers that use them.

- `config.py` / `app_config.py` — typed settings from the environment; `app_config.py`
  is specifically the config handed to a *tenant's running container*, kept separate
  from the control plane's own settings.
- `security.py` — password hashing (Argon2id), JWT issuance.
- `oidc.py` — OIDC/PKCE single sign-on.
- `roles.py` — the action → required-role table backing RBAC.
- `validation.py` — InfraSpec hard caps; namespaces are derived from the **project
  UUID**, never the project name, since two teams may both call a project `staging`.
- `vault.py` — per-user secret store (SSH keypairs, registry credentials), keyed by
  user ID, never returned to another user.
- `sshkeys.py`, `git_credentials.py`, `kubeconfigs.py` — per-user/per-tenant credential
  generation and storage on top of `vault.py`.
- `elk_tenancy.py` — one Elasticsearch role and user per team.
- `redaction.py` — scrubs secret-shaped lines from job logs before they're persisted.
- `repo_url.py` — allowlist validation for `repo_url` (https-only) before it reaches
  `git clone`.
- `pipeline_config.py` — reads a tenant repo's `.platform.yml` pipeline stages.
- `pipeline_graph.py` — pure DB → normalized pipeline-graph contract, no Celery
  dependency, so both routers and workers can build it.
- `tekton_status.py` — translates Tekton `Succeeded` conditions ("True"/"False"/
  "Unknown") into this platform's six-value job status vocabulary. Own that mapping;
  do not re-derive it at a call site.
- `pool.py` — warm cluster pool claiming, to avoid the multi-minute cold provision path.
- `presets.py` — environment size presets.
- `costs.py` — cost estimation from allocated resources × time.
- `runtime.py` — workspace paths and Terraform/Ansible runtime config assembly.
- `logging.py` — shared logging setup (plain or structured JSON) for API and worker.
