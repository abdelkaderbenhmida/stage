# tasks

The task list for the `k8s_reset` role — see `../README.md` for the full breakdown of what
gets destroyed.

- `main.yml` — starts with a comment reiterating this role only runs via
  `ansible-playbook playbook.yml --tags reset` and is never automatic; an interactive
  `pause` prompt confirms before anything destructive happens, then `kubeadm reset -f`,
  directory removal, iptables/ipvs flush, Calico interface removal, service stop, and a
  final `debug` message summarizing what was reset. Every task tagged `reset`.
