#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in docker kind kubectl helm; do command -v "$tool" >/dev/null || { echo "Install $tool first"; exit 1; }; done
if ! kind get clusters | grep -qx emailflow; then kind create cluster --name emailflow --config k8s/kind.yaml; fi
kubectl config use-context kind-emailflow
docker build -t emailflow-backend:local backend
docker build -t emailflow-frontend:local frontend
kind load docker-image --name emailflow emailflow-backend:local emailflow-frontend:local
helm repo add kedacore https://kedacore.github.io/charts --force-update
helm repo update
helm upgrade --install keda kedacore/keda --version 2.17.2 --namespace keda --create-namespace --wait --timeout 10m
kubectl create namespace emailflow --dry-run=client -o yaml | kubectl apply -f -
kubectl -n emailflow create configmap otel-config --from-file=config.yaml=k8s/otel-config.yaml --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f k8s/app.yaml
kubectl apply -f k8s/scaling.yaml
kubectl -n emailflow rollout status deployment/api --timeout=10m
kubectl -n emailflow rollout status deployment/frontend --timeout=5m
kubectl -n emailflow rollout status deployment/gateway --timeout=5m
echo 'Run bash scripts/ports.sh and open http://localhost:8080'
