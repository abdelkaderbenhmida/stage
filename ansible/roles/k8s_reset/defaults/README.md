# defaults

Default variables for the `k8s_reset` role.

- `main.yml` — `k8s_reset_force: false`. The actual opt-in guard against accidental runs
  is `reset_confirmed`, checked in `../../../playbook.yml`'s `when:` clause on this role,
  not here.
