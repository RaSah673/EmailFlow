#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mock_env=$(mktemp)
backup=$(mktemp)
trap 'rm -f -- "$mock_env" "$backup"' EXIT
printf 'fixture\n' > "$backup"
cat > "$mock_env" <<'MOCK'
kind() {
  [[ "$*" == 'get clusters' ]] || return 99
  [[ "$FAILED_READ" != clusters ]] || return 42
  printf 'emailflow\n'
}
kubectl() {
  case " $* " in
    *' config get-contexts '*) return 0 ;;
    *' get namespace '*)
      [[ "$FAILED_READ" != namespace ]] || return 42
      printf 'namespace/emailflow\n' ;;
    *' get statefulset postgres '*) printf 'postgres:16.8' ;;
    *' get deployment api '*) return 0 ;;
    *' get scaledobjects '*) [[ "$FAILED_READ" != scalers ]] || return 42 ;;
    *' get pods '*) [[ "$FAILED_READ" != pods ]] || return 42 ;;
    *' get deployments '*)
      [[ "$FAILED_READ" != deployments ]] || return 42
      printf '0\n' ;;
    *) return 99 ;;
  esac
}
helm() { return 99; }
docker() { return 99; }
MOCK

for failed_read in clusters namespace scalers pods deployments; do
  status=0
  BASH_ENV="$mock_env" FAILED_READ="$failed_read" EMAILFLOW_CLUSTER_NAME=emailflow \
    EMAILFLOW_PYTHON=python EMAILFLOW_UPGRADE=1 EMAILFLOW_UPGRADE_BACKUP="$backup" \
    bash scripts/start-local.sh || status=$?
  if [[ "$status" != 42 ]]; then
    echo "Upgrade did not stop on a failed $failed_read read (exit $status)" >&2
    exit 1
  fi
done
echo 'Upgrade preflight stops on all five failed reads.'
