Verification commands you can run yourself:
# SSH to master
ssh devops@192.168.56.10

# Cluster status
kubectl get nodes -o wide
kubectl get pods -A
kubectl get componentstatuses

# API + DNS
kubectl cluster-info
kubectl run -it --rm dns-test --image=busybox:1.28 --restart=Never -- nslookup kubernetes.default.svc.cluster.local

# Resources
kubectl top nodes
kubectl top pods -A

# Containerd
sudo grep SystemdCgroup /etc/containerd/config.toml

# Events
kubectl get events -A --sort-by='.lastTimestamp' | tail -20

# From host (no SSH)
ansible all -m ping -i ansible/inventory.ini
ansible all -m shell -a "kubectl get nodes" -i ansible/inventory.ini
