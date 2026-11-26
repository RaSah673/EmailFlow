#!/usr/bin/env bash
set -euo pipefail
umask 077
source "$(dirname "$0")/local-env.sh"
mkdir -p backups
file="backups/${EMAILFLOW_CLUSTER_NAME}-$(date -u +%Y%m%dT%H%M%SZ).dump"
set -o noclobber
"${k[@]}" exec postgres-0 -- pg_dump -U emailflow -d emailflow -Fc > "$file"
"${k[@]}" exec -i postgres-0 -- pg_restore --list < "$file" >/dev/null
echo "Database dump: $file (sensitive; encrypt and store separately from keys)"
