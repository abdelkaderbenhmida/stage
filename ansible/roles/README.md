# roles

The five Ansible roles composed by `../playbook.yml` to turn bare Ubuntu VMs into a
working kubeadm cluster, plus a matching teardown role.

- `docker/` — installs Docker CE and containerd on every node, runs first.
- `k8s_common/` — kubeadm prerequisites shared by masters and workers (swap off, kernel
  modules, sysctl, kubelet/kubeadm/kubectl packages).
- `k8s_master/` — `kubeadm init`, Calico CNI install, and worker join-command generation;
  applied only to `[masters]`.
- `k8s_worker/` — fetches the join command from the master and joins the node to the
  cluster; applied only to `[workers]`, one at a time (`serial: 1`).
- `k8s_reset/` — opt-in teardown (`kubeadm reset` + cleanup); only runs with
  `--tags reset -e reset_confirmed=true`, never by default.

`docker` and `k8s_common` have Molecule test scenarios (`molecule/default/`) that run the
role against a throwaway Docker container; `k8s_master`/`k8s_worker`/`k8s_reset` need a
real multi-node cluster and are not covered by Molecule.
