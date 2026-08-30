#!/usr/bin/env bash
set -u
echo "# Pod du meme namespace, etiquette autorisee :"
kubectl run allow-probe --restart=Never --image=curlimages/curl:8.10.1 -n npdemo \
  --labels="app.kubernetes.io/part-of=devops-platform" \
  --command -- sh -c "curl -sm5 -o /dev/null -w 'code=%{http_code}\n' http://users-service.npdemo.svc.cluster.local" >/dev/null
sleep 6
kubectl logs allow-probe -n npdemo
kubectl delete pod allow-probe -n npdemo --wait=false >/dev/null 2>&1
echo
echo "# Pod d'un autre namespace, sans etiquette :"
kubectl run deny-probe --restart=Never --image=curlimages/curl:8.10.1 -n default \
  --command -- sh -c "curl -sm5 -o /dev/null -w 'code=%{http_code}\n' http://users-service.npdemo.svc.cluster.local" >/dev/null
sleep 6
kubectl logs deny-probe -n default
kubectl delete pod deny-probe -n default --wait=false >/dev/null 2>&1
