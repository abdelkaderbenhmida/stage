#!/usr/bin/env bash
# Rotation complete du token racine Vault (scenario 6 du rapport).
#
# Le token est genere localement et n'apparait ni dans les arguments de commande
# ni dans la sortie : il transite par l'entree standard de kubectl.
#   1. nouveau token dans les deux Secrets (namespaces vault et devops-platform)
#   2. redemarrage de Vault, qui repart avec ce token racine
#   3. Job de setup rejoue : moteur KV, politiques et secrets applicatifs
#   4. redemarrage progressif des services, qui relisent le Secret
set -euo pipefail

NOUVEAU=$(head -c 32 /dev/urandom | base64 | tr -d '+/=\n' | head -c 40)

echo "[1/4] ecriture du nouveau token dans les deux Secrets"
for NS in vault devops-platform; do
  printf '%s' "$NOUVEAU" | kubectl create secret generic vault-root-token \
      --namespace "$NS" --from-file=root-token=/dev/stdin \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  echo "      Secret vault-root-token mis a jour dans le namespace $NS"
done

echo "[2/4] redemarrage de Vault avec le nouveau token racine"
kubectl rollout restart deploy/vault -n vault >/dev/null
kubectl rollout status deploy/vault -n vault --timeout=180s

echo "[3/4] rejeu du Job de setup : moteur KV, politiques, secrets applicatifs"
JOB="vault-rotation-$(date +%H%M%S)"
kubectl create job -n vault "$JOB" --from=cronjob/vault-setup-cron >/dev/null
kubectl wait --for=condition=complete "job/$JOB" -n vault --timeout=180s >/dev/null
kubectl logs -n vault "job/$JOB" | tail -2

echo "[4/4] redemarrage progressif des services applicatifs"
kubectl rollout restart deploy -n devops-platform >/dev/null
kubectl rollout status deploy/users-service -n devops-platform --timeout=240s
echo "Rotation terminee — le token n'a jamais ete affiche."
