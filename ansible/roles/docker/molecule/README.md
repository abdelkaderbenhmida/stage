# molecule

Container for this role's Molecule test scenario(s).

- `default/` — the only scenario, run via `molecule test -s default` (or the top-level
  `molecule test --all` invoked by CI's lint job) from `ansible/roles/docker`. Verifies the
  `docker` role converges cleanly against a throwaway Docker container.
