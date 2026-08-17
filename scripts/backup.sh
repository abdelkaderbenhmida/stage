#!/usr/bin/env bash
# Back up the control plane as ONE self-contained unit (docs/TODO.md §7 item 6).
#
# A control-plane backup is only useful if the database and the Terraform
# workspaces come from the same instant: the database says a project owns a
# workspace, and the workspace holds the tfstate that actually controls the
# real infrastructure. Restoring a database from 10:00 next to workspaces from
# 09:00 can orphan or double-create VMs. So both go into a single tarball
# stamped with one timestamp, alongside a MANIFEST describing it.
#
# Env:
#   DATABASE_URL    postgres URL passed to pg_dump          (required)
#   WORKSPACE_ROOT  directory holding per-project workspaces (required)
#   BACKUP_DIR      where units are written                  (required)
#   KEEP            how many units to retain (default 7)
#
# The unit's checksum is written beside it as <unit>.sha256; restore.sh
# refuses to touch a unit that does not match it.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set}"
: "${WORKSPACE_ROOT:?WORKSPACE_ROOT must be set}"
: "${BACKUP_DIR:?BACKUP_DIR must be set}"
KEEP="${KEEP:-7}"

command -v pg_dump >/dev/null || { echo "pg_dump not found on PATH" >&2; exit 1; }

# One stamp for the whole unit — this is the guarantee the restore relies on.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
UNIT="${BACKUP_DIR}/cp-${STAMP}.tar.gz"

mkdir -p "$BACKUP_DIR"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# 1. Database. Custom format so restore.sh can use pg_restore --clean.
pg_dump --format=custom --no-owner --no-acl \
        --file="${STAGE}/controlplane.dump" "$DATABASE_URL"

# 2. Workspaces. Archived with their parent directory name as the root entry
#    so the tree is unambiguous when it is unpacked somewhere else.
WS_PARENT="$(cd "$(dirname "$WORKSPACE_ROOT")" && pwd)"
WS_NAME="$(basename "$WORKSPACE_ROOT")"
if [ -d "$WORKSPACE_ROOT" ]; then
  tar -czf "${STAGE}/workspaces.tar.gz" -C "$WS_PARENT" "$WS_NAME"
else
  # No workspaces yet is a legitimate state (fresh install); keep the member
  # present so restore.sh never has to special-case its absence.
  mkdir -p "${STAGE}/empty/${WS_NAME}"
  tar -czf "${STAGE}/workspaces.tar.gz" -C "${STAGE}/empty" "$WS_NAME"
  rm -rf "${STAGE}/empty"
fi

# 3. Manifest — what this unit is, and what the parts hash to, so a restore
#    can tell "corrupted" apart from "truncated" without guessing.
{
  echo "stamp=${STAMP}"
  echo "created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "workspace_root=${WORKSPACE_ROOT}"
  echo "database=$(printf '%s' "$DATABASE_URL" | sed -E 's#://[^@]*@#://***@#')"
  echo "tool=backup.sh"
  echo "members:"
  ( cd "$STAGE" && sha256sum controlplane.dump workspaces.tar.gz )
} > "${STAGE}/MANIFEST"

tar -czf "$UNIT" -C "$STAGE" controlplane.dump workspaces.tar.gz MANIFEST

# Checksum of the unit itself, stored beside it — a byte flipped anywhere in
# the tarball is caught before anything is written back over live data.
( cd "$BACKUP_DIR" && sha256sum "$(basename "$UNIT")" > "$(basename "$UNIT").sha256" )

# Retention: keep the newest $KEEP units, drop older ones with their sidecars.
mapfile -t UNITS < <(ls -1 "${BACKUP_DIR}"/cp-*.tar.gz 2>/dev/null | sort -r)
if [ "${#UNITS[@]}" -gt "$KEEP" ]; then
  for old in "${UNITS[@]:$KEEP}"; do
    rm -f "$old" "${old}.sha256"
  done
fi

echo "backup unit written: $UNIT"
