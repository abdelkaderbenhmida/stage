# molecule

Container for this role's Molecule test scenario(s).

- `default/` — the only scenario, run via `molecule test -s default` (or CI's
  `molecule test --all`) from `ansible/roles/k8s_common`. Verifies the role converges
  cleanly against a throwaway Docker container; note the sysctl/kernel-module tasks touch
  host-level settings that can behave differently inside the Molecule container than on a
  real VM.
