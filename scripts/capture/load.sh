#!/usr/bin/env bash
# Charge soutenue sur users-service pendant N secondes (defaut 420).
set -u
DUREE="${1:-420}"; CONC="${2:-250}"
kubectl -n devops-platform port-forward svc/users-service 18080:80 >/dev/null 2>&1 &
PF=$!
sleep 4
fin=$(( $(date +%s) + DUREE ))
while [ "$(date +%s)" -lt "$fin" ]; do
  ab -k -c "$CONC" -n 20000 -q "http://127.0.0.1:18080/users" >/dev/null 2>&1
done
kill $PF 2>/dev/null
