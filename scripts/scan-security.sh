#!/usr/bin/env bash
# Run every scanner even if findings occur. Nonzero status means findings or tool errors.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p security/reports
status=0
run(){ local report="$1"; shift; echo "=== $report ==="; "$@" >"security/reports/$report.log" 2>&1 || { status=1; echo "FAILED: inspect security/reports/$report.log"; }; }
run trivy-source trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --exit-code 1 \
  --skip-dirs security/reports --skip-dirs .git --skip-dirs .venv-security --skip-dirs .venv \
  --skip-dirs frontend/node_modules --skip-dirs frontend/dist --skip-dirs emailflow-v2 --skip-dirs backups .
run bandit bandit -r backend/app embedding -ll
run semgrep semgrep scan --metrics=off --config p/default --config p/security-audit --error backend embedding frontend/src scripts
run backend-dependencies pip-audit -r backend/requirements.txt
run embedding-dependencies pip-audit -r embedding/requirements.txt
run npm-audit npm --prefix frontend audit --audit-level=high
run k8s-config trivy config --severity HIGH,CRITICAL --exit-code 1 k8s
images=$(grep -hE '^ *image: ' k8s/{app,database}.yaml | awk '{print $2}' | sort -u)
images=$(printf '%s\n%s\n' "$images" 'ghcr.io/kedacore/keda:2.17.2' 'ghcr.io/kedacore/keda-metrics-apiserver:2.17.2' 'ghcr.io/kedacore/keda-admission-webhooks:2.17.2')
# Include KEDA and any live workload image if the local cluster exists.
context="kind-${EMAILFLOW_CLUSTER_NAME:-emailflow}"
if command -v kubectl >/dev/null && kubectl --context "$context" get ns keda >/dev/null 2>&1; then
 images=$(printf '%s\n%s\n' "$images" "$(kubectl --context "$context" get pods -A -o jsonpath='{range .items[*].spec.containers[*]}{.image}{"\n"}{end}')")
fi
images=$(printf '%s\n' "$images" | sort -u)
while IFS= read -r image; do
 [[ -n "$image" ]] || continue
 name=$(printf '%s' "$image" | tr '/:@' '___')
 # Local app images must exist on the Docker host. External images can be fetched by Trivy.
 run "image-$name" trivy image --scanners vuln,secret --severity HIGH,CRITICAL --exit-code 1 "$image"
 run "sbom-$name" trivy image --format cyclonedx --output "security/reports/$name.cdx.json" "$image"
done <<< "$images"
echo "Security report directory: security/reports (exit=$status)"
exit "$status"
