#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/local-env.sh"
pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT
for pair in "frontend ${EMAILFLOW_DASHBOARD_PORT:-8080}:80" "mailcatcher ${EMAILFLOW_MAIL_PORT:-1080}:1080" "jaeger ${EMAILFLOW_JAEGER_PORT:-16686}:16686"; do
 read -r svc ports <<< "$pair"
 "${k[@]}" port-forward --address 127.0.0.1 "svc/$svc" "$ports" & pids+=("$!")
done
echo "Dashboard http://localhost:${EMAILFLOW_DASHBOARD_PORT:-8080} | MailCatcher http://localhost:${EMAILFLOW_MAIL_PORT:-1080} | Jaeger http://localhost:${EMAILFLOW_JAEGER_PORT:-16686}"
wait -n
