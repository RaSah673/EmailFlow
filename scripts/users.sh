#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/local-env.sh"
exec "$emailflow_python" scripts/manage-users.py "$@"
