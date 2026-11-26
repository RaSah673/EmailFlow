#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/local-env.sh"
read -r -p "Delete the $EMAILFLOW_CLUSTER_NAME Kind cluster and ALL local data? [y/N] " answer
if [[ "$answer" == 'y' ]]; then kind delete cluster --name "$EMAILFLOW_CLUSTER_NAME" --kubeconfig "$KUBECONFIG"; fi
