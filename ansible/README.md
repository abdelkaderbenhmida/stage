# ansible

Provisioning for the platform's Kubernetes VM-mode clusters: a single playbook waits for
node SSH to come up, installs Docker/containerd, applies kubeadm prerequisites, bootstraps
the control-plane master (kubeadm init + Calico), joins workers, and offers an opt-in
cluster reset. This is what the control-plane's VM-mode path runs against freshly created
libvirt/terraform VMs before handing a kubeconfig back to the app.

- `playbook.yml` — the top-level play sequence: wait-for-SSH, `docker` role on all hosts,
  `k8s_common` on all hosts, `k8s_master` on `[masters]`, `k8s_worker` on `[workers]`
  (`serial: 1`), and `k8s_reset` gated behind `--tags reset` **and**
  `-e reset_confirmed=true` (tagged `never` so it never runs by default).
- `ansible.cfg` — inventory/connection defaults: `devops` remote user, ed25519 key,
  `host_key_checking = False` (disposable homelab VMs), pipelining, smart fact gathering.
- `group_vars/all.yml` — cluster-wide variables (k8s version, pod/service CIDRs, Calico
  version, containerd socket) shared by every role.
- `inventory.ini.example` — sample `[masters]`/`[workers]` inventory to copy to
  `inventory.ini`.
- `requirements.yml` — Galaxy collection deps (`community.general`, needed by
  `k8s_common`'s `modprobe` task); install with `ansible-galaxy collection install -r requirements.yml`.
- `molecule.yml` — shared Molecule test config (Docker driver) used by role-level
  `molecule/default` scenarios; see `roles/`.
- `roles/` — the five roles the playbook composes (`docker`, `k8s_common`, `k8s_master`,
  `k8s_worker`, `k8s_reset`).
