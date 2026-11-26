#!/usr/bin/env bash
# Source from an EmailFlow operating script.
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export EMAILFLOW_CLUSTER_NAME="${EMAILFLOW_CLUSTER_NAME:-emailflow}"
if [[ ! "$EMAILFLOW_CLUSTER_NAME" =~ ^emailflow(-[a-z0-9]+)*$ ]]; then
  echo 'Use a dedicated Kind cluster named emailflow or emailflow-<suffix>.' >&2
  exit 2
fi
export KUBECONFIG="${KUBECONFIG:-$PWD/.emailflow-kubeconfig}"
emailflow_context="kind-$EMAILFLOW_CLUSTER_NAME"
k=(kubectl --context "$emailflow_context" -n emailflow)
emailflow_python="${EMAILFLOW_PYTHON:-python3}"
