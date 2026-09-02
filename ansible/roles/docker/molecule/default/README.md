# default

Molecule "default" scenario for the `docker` role.

- `molecule.yml` — present but empty (0 bytes). Molecule falls back to the shared
  top-level `ansible/molecule.yml` (Docker driver, `geerlingguy/docker-ubuntu2404-ansible`
  image, `ansible` provisioner/verifier) for this scenario's configuration; the empty file
  just marks the scenario directory as valid so `molecule test -s default` finds it here.
