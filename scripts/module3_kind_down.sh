#!/usr/bin/env bash
set -euo pipefail
CLUSTER_NAME="${CLUSTER_NAME:-module3}"
NS="${NAMESPACE:-ai-mesh-m3}"

if command -v helm >/dev/null; then
  helm uninstall module3-k8s -n "$NS" 2>/dev/null || true
fi
kubectl delete ns "$NS" --ignore-not-found 2>/dev/null || true

if command -v kind >/dev/null && kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  kind delete cluster --name "$CLUSTER_NAME"
  echo "Deleted Kind cluster $CLUSTER_NAME"
else
  echo "No Kind cluster $CLUSTER_NAME (or kind missing)"
fi
