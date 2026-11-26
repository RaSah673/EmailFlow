#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p security/reports
# Docker host networking: native Linux or Docker Desktop with host networking enabled.
# Run only against the local synthetic environment, with ports.sh active.
port="${EMAILFLOW_DASHBOARD_PORT:-8080}"
[[ "$port" =~ ^[0-9]{1,5}$ ]] && ((10#$port > 0 && 10#$port < 65536)) || { echo 'Invalid loopback port' >&2; exit 2; }
docker run --rm --network host -v "$PWD/security/reports:/zap/wrk:rw" ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t "http://127.0.0.1:$port" -r zap.html -J zap.json
