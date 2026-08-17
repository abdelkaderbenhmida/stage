#!/usr/bin/env bash
# Restore a control-plane unit written by backup.sh (docs/TODO.md §7 item 6).
#
# Usage: restore.sh <unit.tar.gz>
#
# Env:
#   DATABASE_URL    postgres URL passed to pg_restore        (required)
#   WORKSPACE_ROOT  directory to restore workspaces into     (required)
#   FORCE           1 to overwrite an existing WORKSPACE_ROOT (default 0)
#
# Restoring is destructive in two directions at once — it replaces database
# contents and the Terraform workspaces that own real infrastructure. So it
# fails closed: the unit's checksum must match, its MANIFEST stamp must match
# its filename, and a non-empty target workspace needs FORCE=1.
set -euo pipefail

UNIT="${1:-}"
[ -n "$UNIT" ] || { echo "usage: restore.sh <unit.tar.gz>" >&2; exit 2; }
[ -f "$UNIT" ] || { echo "no such backup unit: $UNIT" >&2; exit 2; }

: "${DATABASE_URL:?DATABASE_URL must be set}"
: "${WORKSPACE_ROOT:?WORKSPACE_ROOT must be set}"
FORCE="${FORCE:-0}"

command -v pg_restore >/dev/null || { echo "pg_restore not found on PATH" >&2; exit 1; }

# 1. Integrity. A corrupted unit must never reach live data, so this is the
#    first thing checked and it is not overridable by FORCE.
SUM_FILE="${UNIT}.sha256"
if [ -f "$SUM_FILE" ]; then
  if ! ( cd "$(dirname "$UNIT")" && sha256sum -c --status "$(basename "$SUM_FILE")" ); then
    echo "checksum mismatch for $(basename "$UNIT") — refusing to restore" >&2
    exit 1
  fi
else
  echo "no checksum sidecar for $(basename "$UNIT") — refusing to restore" >&2
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

if ! tar -xzf "$UNIT" -C "$STAGE" 2>/dev/null; then
  echo "unit is not a readable archive — refusing to restore" >&2
  exit 1
fi

for member in controlplane.dump workspaces.tar.gz MANIFEST; do
  [ -f "${STAGE}/${member}" ] || { echo "unit is missing ${member} — refusing to restore" >&2; exit 1; }
done

# 2. Same-stamp guarantee: the database dump and the workspaces in this unit
#    were taken together, and the filename must agree with the manifest.
STAMP="$(sed -n 's/^stamp=//p' "${STAGE}/MANIFEST")"
EXPECTED="cp-${STAMP}.tar.gz"
if [ "$(basename "$UNIT")" != "$EXPECTED" ]; then
  echo "manifest stamp ${STAMP} does not match $(basename "$UNIT") — refusing to restore" >&2
  exit 1
fi

# 3. Don't silently destroy a populated workspace root.
if [ -d "$WORKSPACE_ROOT" ] && [ -n "$(ls -A "$WORKSPACE_ROOT" 2>/dev/null)" ] && [ "$FORCE" != "1" ]; then
  echo "$WORKSPACE_ROOT is not empty — re-run with FORCE=1 to overwrite" >&2
  exit 1
fi

# 4. Database. --clean --if-exists so a restore over an existing schema
#    replaces it rather than erroring on every duplicate object.
pg_restore --clean --if-exists --no-owner --no-acl \
           --dbname="$DATABASE_URL" "${STAGE}/controlplane.dump"

# 5. Workspaces. The archive's root entry is the original directory name, so
#    strip it and land the contents directly in WORKSPACE_ROOT.
rm -rf "$WORKSPACE_ROOT"
mkdir -p "$WORKSPACE_ROOT"
tar -xzf "${STAGE}/workspaces.tar.gz" -C "$WORKSPACE_ROOT" --strip-components=1

echo "restored unit ${STAMP} into ${WORKSPACE_ROOT}"
