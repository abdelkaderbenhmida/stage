Objective
- Bootstrap 1-master / 2-worker Kubernetes 1.28 cluster on Ubuntu 22.04 VMs using Ansible (kubeadm + containerd + Calico)
Important Details
- Provider: dmacvicar/libvirt v0.9.8 — create.content.url ignores capacity, required manual virsh vol-resize + cloud-init growpart to get 20G root disks
- Ansible roles use tags: [docker, k8s, master, worker, reset] with never — each must be invoked explicitly via --tags
- cloud-init.tpl now includes growpart + resizefs modules for future fresh VMs
- Kubernetes repo: https://pkgs.k8s.io/core:/stable:/v1.28/deb/ /, kubelet/kubeadm/kubectl pinned to 1.28.*
- Calico v3.26.1 manifests applied (tigera-operator + custom-resources)
Work State
- Completed: Terraform VMs (20G disks, growpart working), Ansible docker role (Docker CE + containerd + SystemdCgroup=true), Ansible k8s_common role (swap off, sysctl, modules, k8s packages), kubeadm init (partial — API server, etcd, controller-manager, scheduler static pods running), kube-proxy addon installed manually, Calico manifests applied
- Active: Cluster in broken state — kubectl returns connection refused to 192.168.56.10:6443; API server and etcd keep restarting (both at ATTEMPT=2); logs show API server cannot connect to etcd at 127.0.0.1:2379 despite etcd reporting serving client traffic securely on that address
- Blocked: k8s_master role task Run kubeadm init on master creates /etc/kubernetes/admin.conf — subsequent runs will skip due to creates guard; cluster needs functional API server before worker join or kubectl get nodes
Next Move
1. Diagnose and fix API server/etcd crash loop — likely check kube-apiserver pod logs for root cause, verify etcd health directly (etcdctl endpoint health), or run kubeadm reset -f on master and re-init cleanly
2. If reset needed: ansible-playbook -i inventory.ini playbook.yml --tags reset on all nodes, then re-run --tags master and --tags worker
