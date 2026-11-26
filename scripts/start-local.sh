#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/local-env.sh"
for tool in docker kind kubectl helm "$emailflow_python"; do command -v "$tool" >/dev/null || { echo "Install $tool first"; exit 1; }; done

clusters=$(kind get clusters)
if ! grep -Fxq "$EMAILFLOW_CLUSTER_NAME" <<< "$clusters"; then
  kind create cluster --name "$EMAILFLOW_CLUSTER_NAME" --kubeconfig "$KUBECONFIG" --config k8s/kind.yaml
elif ! kubectl config get-contexts "$emailflow_context" >/dev/null 2>&1; then
  kind export kubeconfig --name "$EMAILFLOW_CLUSTER_NAME" --kubeconfig "$KUBECONFIG"
fi

image=''
legacy_api=''
namespace=$(kubectl --context "$emailflow_context" get namespace emailflow --ignore-not-found -o name)
if [[ -n "$namespace" ]]; then
  image=$("${k[@]}" get statefulset postgres --ignore-not-found -o jsonpath='{.spec.template.spec.containers[0].image}')
  legacy_api=$("${k[@]}" get deployment api --ignore-not-found -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="DATABASE_URL")].value}')
fi
migration_args=()
if [[ -n "$image" && ( "$image" != *pgvector/pgvector:* || -n "$legacy_api" || "${EMAILFLOW_UPGRADE:-}" == 1 ) ]]; then
  if [[ "${EMAILFLOW_UPGRADE:-}" != 1 || ! -s "${EMAILFLOW_UPGRADE_BACKUP:-}" ]]; then
    echo 'Existing v1 database detected. See docs/OPERATIONS.md for backup, drain, and migration commands.' >&2
    exit 2
  fi
  # Keep cluster reads outside conditionals so any failed read stops the migration.
  scalers=$("${k[@]}" get scaledobjects -o name)
  if [[ -n "$scalers" ]]; then
    echo 'Remove the two worker ScaledObjects after draining v1 before migrating.' >&2
    exit 2
  fi
  writers='app in (api,scheduler,gateway,worker-standard,worker-urgent,mirror,archive)'
  writer_pods=$("${k[@]}" get pods -l "$writers" -o name)
  writer_replicas=$("${k[@]}" get deployments -l "$writers" -o jsonpath='{range .items[*]}{.spec.replicas}{"\n"}{end}')
  if [[ -n "$writer_pods" || "$writer_replicas" =~ [1-9] ]]; then
    echo 'Scale v1 writers to zero and wait for their pods to terminate.' >&2
    exit 2
  fi
  "${k[@]}" exec -i postgres-0 -- pg_restore --list < "$EMAILFLOW_UPGRADE_BACKUP" >/dev/null
  migration_args=(--rotate-owner)
fi

for component in backend frontend embedding; do
  docker build -t "emailflow-$component:local" "$component"
done
kind load docker-image --name "$EMAILFLOW_CLUSTER_NAME" emailflow-backend:local emailflow-frontend:local emailflow-embedding:local
helm upgrade --install keda keda --repo https://kedacore.github.io/charts --version 2.17.2 \
  --kube-context "$emailflow_context" --namespace keda --create-namespace --wait --timeout 10m

"${k[@]}" create namespace emailflow --dry-run=client -o yaml | "${k[@]}" apply -f -
"${k[@]}" create configmap otel-config --from-file=config.yaml=k8s/otel-config.yaml --dry-run=client -o yaml | "${k[@]}" apply -f -
"${k[@]}" create configmap emailflow-config --from-literal=APP_ORIGIN="${EMAILFLOW_ORIGIN:-http://localhost:8080}" \
  --dry-run=client -o yaml | "${k[@]}" apply -f -
"$emailflow_python" scripts/provision-secrets.py
"${k[@]}" apply -f k8s/database.yaml
"${k[@]}" rollout status statefulset/postgres --timeout=10m
"$emailflow_python" scripts/migrate-db.py "${migration_args[@]}"
"${k[@]}" apply -f k8s/network-policy.yaml
"${k[@]}" apply -f k8s/app.yaml
"${k[@]}" apply -f k8s/scaling.yaml
if [[ -n "$image" ]]; then
  "${k[@]}" rollout restart deployment/api deployment/gateway deployment/scheduler deployment/worker-standard \
    deployment/worker-urgent deployment/mirror deployment/archive deployment/embedding deployment/frontend
fi
"${k[@]}" rollout status deployment/api deployment/frontend deployment/gateway deployment/embedding --timeout=10m
echo 'Create the first administrator with bash scripts/users.sh create admin --role admin, then run bash scripts/ports.sh.'
