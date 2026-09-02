# default

Molecule "default" scenario for the `k8s_common` role.

- `molecule.yml` — present but empty (0 bytes), same pattern as `roles/docker`'s scenario;
  configuration is inherited from the shared top-level `ansible/molecule.yml` (Docker
  driver, `geerlingguy/docker-ubuntu2404-ansible` image, `ansible` provisioner/verifier).
