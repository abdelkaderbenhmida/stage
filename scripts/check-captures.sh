#!/usr/bin/env bash
# Liste les captures encore manquantes sous images/ par rapport a rapport.tex.
set -euo pipefail
cd "$(dirname "$0")/.."
missing=0; total=0
while read -r name; do
  total=$((total+1))
  if [ ! -f "images/${name}.png" ] && [ ! -f "images/${name}.jpg" ] && [ ! -f "images/${name}.pdf" ]; then
    echo "MANQUANT: images/${name}.png"
    missing=$((missing+1))
  fi
done < <(grep -o '{images/[a-z0-9-]*}' rapport.tex | tr -d '{}' | sed 's|images/||' | sort -u)
echo "---"
echo "${missing} manquantes / ${total} figures referencees"
