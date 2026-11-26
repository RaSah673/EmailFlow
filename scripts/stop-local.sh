#!/usr/bin/env bash
set -euo pipefail
read -r -p 'Delete the emailflow Kind cluster and ALL local data? [y/N] ' answer
[[ "$answer" == 'y' ]] && kind delete cluster --name emailflow || true
