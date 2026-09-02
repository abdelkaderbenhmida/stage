# k8s_worker

Joins `[workers]` hosts to the cluster bootstrapped by `k8s_master`. Applied with
`serial: 1` in `../../playbook.yml` so nodes join one at a time. No Molecule scenario —
needs a real master to join against.

- `tasks/main.yml` — `slurp`s `/tmp/kubeadm-join.txt` from the first `[masters]` host
  (`delegate_to: "{{ groups['masters'][0] }}"`), base64-decodes it, asserts it actually
  contains `kubeadm join` (catches an incomplete master bootstrap early with a clear
  failure message), then runs it with `ansible.builtin.command` — deliberately not
  `shell:`, since the join command is a fixed argv vector from the master and needs no
  shell metacharacter interpretation (injection-safe at the task layer). Restarts
  `kubelet`, then polls (delegated to the master) until
  `kubectl get node <host> -o jsonpath=...Ready...` reports `True`.
- `defaults/main.yml` — only `cri_endpoint`; no worker-specific vars because the join
  command itself (fetched from the master) carries everything else.
- `meta/main.yml` — Galaxy metadata.
