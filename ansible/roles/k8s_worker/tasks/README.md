# tasks

The task list for the `k8s_worker` role.

- `main.yml` — fetches and decodes the join command from the master (delegated `slurp`),
  asserts it's valid, runs it with `command:` (not `shell:` — see `../README.md` for why
  that matters), restarts `kubelet`, and polls the master until the new node reports
  `Ready`. Every task tagged `worker`.
