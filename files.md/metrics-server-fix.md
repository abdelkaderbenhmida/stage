# Metrics Server Install + Fix (K8s 1.28 + self-signed kubelet certs)

```bash
# 1. install
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# 2. patch TLS skip
kubectl patch deployment metrics-server -n kube-system --type='json' \
  -p='[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"}]'

# 3. wait ready
kubectl wait --for=condition=available --timeout=120s deployment/metrics-server -n kube-system

# 4. verify
kubectl top nodes
kubectl top pods -A
```

## Debug

```bash
kubectl get pods -n kube-system | grep metrics
kubectl logs -n kube-system deployment/metrics-server
kubectl describe pod -n kube-system -l app.kubernetes.io/name=metrics-server
```

## Context fix (if kubectl targets wrong cluster)

```bash
kubectl config current-context
kubectl config get-contexts

# copy config from master-01
scp devops@192.168.56.10:~/.kube/config ~/.kube/config-stage
kubectl --kubeconfig ~/.kube/config-stage top nodes

# or set as default
export KUBECONFIG=~/.kube/config-stage
```
