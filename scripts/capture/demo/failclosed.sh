#!/usr/bin/env bash
# Demonstration du comportement fail-closed au demarrage (chapitre secrets).
#
# Vault est arrete, puis un pod est recree : sans secret disponible, le service
# refuse de demarrer au lieu de servir avec des valeurs de repli.
set -u
echo "# arret de Vault"
kubectl scale deploy/vault -n vault --replicas=0 >/dev/null
sleep 10

POD=$(kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service \
        -o jsonpath='{.items[0].metadata.name}')
echo "# suppression du pod $POD : il va etre recree sans acces a Vault"
kubectl delete pod -n devops-platform "$POD" --wait=false >/dev/null
sleep 75

kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service
echo
NEW=$(kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service \
        --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
echo "# journal de l init container vault-login, qui garde la porte fermee :"
kubectl logs -n devops-platform "$NEW" -c vault-login --tail=8 2>/dev/null \
  || kubectl logs -n devops-platform "$NEW" -c vault-login --previous --tail=8 2>/dev/null
echo
kubectl describe pod -n devops-platform "$NEW" \
  | grep -A3 "Init Containers:" | head -4
