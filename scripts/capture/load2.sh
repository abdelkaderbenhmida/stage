#!/usr/bin/env bash
# Charge additionnelle : reutilise le port-forward deja ouvert sur 18080.
set -u
DUREE="${1:-400}"; CONC="${2:-250}"
fin=$(( $(date +%s) + DUREE ))
while [ "$(date +%s)" -lt "$fin" ]; do
  ab -k -c "$CONC" -n 20000 -q "http://127.0.0.1:18080/users" >/dev/null 2>&1
done
