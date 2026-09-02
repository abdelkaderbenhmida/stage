# schemas

- `spec.py` — Pydantic v2 models for the `InfraSpec` document and every validation rule
  around it: `NodeSpec`, `NetworkSpec`, `ConfigSpec`, `InfraSpec`, and
  `SpecValidationError`. All models use `extra="forbid"` so unknown keys are rejected
  rather than silently ignored. `InfraSpec` is the input both the Terraform/Ansible
  renderers (VM-mode) and the namespace renderer (namespace-mode) consume; hard caps on
  its values live in `controlplane/core/validation.py`.
