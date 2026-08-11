#!/usr/bin/env bash
# DevOps Central Platform — Regenerate kubelet cAdvisor scrape endpoints.
#
# Prometheus scrapes kubelet /metrics/cadvisor on each node (port 10250). Node
# IPs are dynamic (AWS EC2 / libvirt), so the Endpoints block in
# k8s/monitoring/kubelet/kubelet-scrape.yaml is rendered from the Ansible
# inventory instead of being hardcoded. Run this after every cluster (re)build:
#
#   scripts/generate-inventory.sh   # refresh ansible/inventory.ini from Terraform
#   scripts/generate-kubelet-scrape.sh
#   kubectl apply -f k8s/monitoring/kubelet/kubelet-scrape.yaml
#
# IP selection: private_ip from the inventory when present, else ansible_host.
# Exit codes: 0 ok · 1 inventory missing/empty · 2 placeholder missing · 3 usage
#
# The rest of the file (Service + ServiceMonitor) is left untouched.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INVENTORY="${INVENTORY:-${REPO_ROOT}/ansible/inventory.ini}"
TARGET="${TARGET:-${REPO_ROOT}/k8s/monitoring/kubelet/kubelet-scrape.yaml}"
PLACEHOLDER="__NODE_IPS__"

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help) sed -n '2,14p' "$0" ; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2 ; exit 3 ;;
  esac
fi

if [[ ! -f "${INVENTORY}" ]]; then
  echo "ERROR: inventory not found: ${INVENTORY}" >&2
  echo "  Run scripts/generate-inventory.sh first." >&2
  exit 1
fi

ips=()
current_group=""
while IFS= read -r line || [[ -n "${line}" ]]; do
  case "${line}" in
    '['*)
      current_group="${line}"
      ;;
    [A-Za-z0-9_-]*[[:space:]]ansible_host=*)
      # only nodes in [masters] / [workers]
      case "${current_group}" in
        '['masters']'|'['workers']') ;;
        *) continue ;;
      esac
      host=$(awk '{ for (i = 1; i <= NF; i++) {
                     if ($i ~ /^private_ip=/) { sub(/^private_ip=/, "", $i); print $i; found=1; break } }
                   if (!found) { for (i = 1; i <= NF; i++)
                     if ($i ~ /^ansible_host=/) { sub(/^ansible_host=/, "", $i); print $i; break } } }' <<<"${line}")
      if [[ -n "${host}" ]]; then
        ips+=("- ip: ${host}")
      fi
      ;;
  esac
done < "${INVENTORY}"

if [[ ${#ips[@]} -eq 0 ]]; then
  echo "ERROR: no node IPs found in ${INVENTORY} (check [masters]/[workers] groups)." >&2
  exit 1
fi

if ! grep -q "${PLACEHOLDER}" "${TARGET}"; then
  echo "ERROR: placeholder '${PLACEHOLDER}' missing from ${TARGET}" >&2
  exit 2
fi

# Splice generated IPs into the placeholder line (keeps Service/ServiceMonitor intact).
printf '%s\n' "${ips[@]}" > /tmp/kubelet-ips.$$
python3 - "${TARGET}" "${PLACEHOLDER}" /tmp/kubelet-ips.$$ <<'PYEOF'
import sys

target, placeholder, ipfile = sys.argv[1], sys.argv[2], sys.argv[3]
with open(target) as fh:
    text = fh.read()
if placeholder not in text:
    sys.exit(f"ERROR: placeholder '{placeholder}' missing from {target}")
with open(ipfile) as fh:
    lines = [line.rstrip("\n") for line in fh]
# Continuation lines must carry the placeholder line's indentation (6 spaces).
indent = " " * 6
rendered = ("\n" + indent).join(lines)
with open(target, "w") as fh:
    fh.write(text.replace(placeholder, rendered))
PYEOF
rm -f /tmp/kubelet-ips.$$
echo "✓ kubelet scrape Endpoints updated in ${TARGET}"
printf '  %s\n' "${ips[@]}"
