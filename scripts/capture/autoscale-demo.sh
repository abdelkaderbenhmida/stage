#!/usr/bin/env bash
# Demonstration complete de l'auto-scaling : attend le retour a 2 repliques,
# lance la charge, et laisse le releve HPA tourner pendant la montee.
set -u
cd "$(dirname "$0")/../.."
echo "attente du retour a 2 repliques..."
for i in $(seq 1 60); do
  r=$(kubectl get deploy users-service -n devops-platform -o jsonpath='{.spec.replicas}')
  [ "$r" = "2" ] && break
  sleep 15
done
echo "repliques = $(kubectl get deploy users-service -n devops-platform -o jsonpath='{.spec.replicas}')"
./scripts/capture/load.sh 600 250 &
sleep 25
./scripts/capture/load2.sh 560 250 &
sleep 5
python3 scripts/capture/shots.py scenario4-01
wait
