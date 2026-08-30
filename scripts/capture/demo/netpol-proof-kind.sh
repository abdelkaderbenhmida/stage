#!/usr/bin/env bash
set -u
echo "# Pod du meme namespace, etiquette autorisee :"
kubectl run allow-probe --restart=Never --image=curlimages/curl:8.10.1 -n devops-platform \
  --labels="app.kubernetes.io/part-of=devops-platform" \
  --overrides='{"spec":{"containers":[{"name":"allow-probe","image":"curlimages/curl:8.10.1","command":["sh","-c","curl -sm5 -o /dev/null -w code=%{http_code} http://users-service/livez"],"securityContext":{"allowPrivilegeEscalation":false,"runAsNonRoot":true,"runAsUser":10001,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}}]}}' >/dev/null
sleep 6
kubectl logs allow-probe -n devops-platform
kubectl delete pod allow-probe -n devops-platform --wait=false >/dev/null 2>&1
echo
echo "# Pod d'un autre namespace, sans etiquette :"
kubectl run deny-probe --restart=Never --image=curlimages/curl:8.10.1 -n default \
  --overrides='{"spec":{"containers":[{"name":"deny-probe","image":"curlimages/curl:8.10.1","command":["sh","-c","curl -sm5 -o /dev/null -w code=%{http_code} http://users-service.devops-platform.svc.cluster.local"]}]}}' >/dev/null
sleep 6
kubectl logs deny-probe -n default
kubectl delete pod deny-probe -n default --wait=false >/dev/null 2>&1
