#!/usr/bin/env bash
set -euo pipefail
pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT
for pair in 'frontend 8080:80' 'mailcatcher 1080:1080' 'jaeger 16686:16686'; do
 read -r svc ports <<< "$pair"
 kubectl --context kind-emailflow -n emailflow port-forward --address 127.0.0.1 "svc/$svc" "$ports" & pids+=("$!")
done
echo 'Dashboard: http://localhost:8080 | MailCatcher: http://localhost:1080 | Jaeger: http://localhost:16686'
wait -n
