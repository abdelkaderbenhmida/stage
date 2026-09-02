#!/usr/bin/env bash
# Port-forwards + proxy d'authentification, dans un seul processus superviseur.
set -u
pkill -f "kubectl port-forward" 2>/dev/null
pkill -f "capture/uiproxy.py" 2>/dev/null
sleep 2
kubectl port-forward -n monitoring svc/grafana 3000:3000 &
kubectl port-forward -n monitoring svc/prometheus 9090:9090 &
kubectl port-forward -n monitoring svc/alertmanager 9093:9093 &
kubectl port-forward -n monitoring svc/kibana 5601:5601 &
kubectl port-forward -n monitoring svc/elasticsearch 9200:9200 &
kubectl port-forward -n argocd svc/argocd-server 8480:80 &
kubectl port-forward -n devops-platform svc/users-service 18080:80 &
sleep 6
python3 "$(dirname "$0")/uiproxy.py" &
wait
