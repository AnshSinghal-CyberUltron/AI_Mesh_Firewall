#!/usr/bin/env bash
# Bootstrap Kind + Cilium + Module 3 Phase 2 Helm chart (Windows Git Bash / WSL / Linux).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$ROOT/.tools:$PATH"
CLUSTER_NAME="${CLUSTER_NAME:-module3}"
NS="${NAMESPACE:-ai-mesh-m3}"
CONTROL_URL="${CONTROL_URL:-http://host.docker.internal:8100}"
AGENT_API_KEY="${AGENT_API_KEY:?Set AGENT_API_KEY}"
ORG_SLUG="${ORG_SLUG:-zeroshield}"
CHART="$ROOT/deploy/module3-k8s"

command -v kind >/dev/null || { echo "kind not found — install https://kind.sigs.k8s.io/ (or place binary in .tools/)"; exit 1; }
command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }
command -v helm >/dev/null || { echo "helm not found — install https://helm.sh/ (or place binary in .tools/)"; exit 1; }
command -v docker >/dev/null || { echo "docker not found"; exit 1; }

echo "==> build agent + river + phase3 images"
docker build -t module3-mesh-agent:0.1.0 "$CHART/agent"
docker build -t module3-river:0.1.0 "$CHART/river"
docker build -t module3-state-sync:0.1.0 "$CHART/state-sync"
docker build -t module3-authz-shim:0.1.0 "$CHART/authz-shim"

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo "==> create Kind cluster $CLUSTER_NAME"
  # Default CNI: reliable on Docker Desktop/WSL2. Set KIND_USE_CILIUM=1 for Cilium (Linux/VM).
  if [[ "${KIND_USE_CILIUM:-0}" == "1" ]]; then
    cat <<EOF | kind create cluster --name "$CLUSTER_NAME" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  disableDefaultCNI: true
  kubeProxyMode: none
nodes:
  - role: control-plane
EOF
  else
    kind create cluster --name "$CLUSTER_NAME"
  fi
else
  echo "==> Kind cluster $CLUSTER_NAME already exists"
fi

echo "==> load images into Kind"
kind load docker-image module3-mesh-agent:0.1.0 --name "$CLUSTER_NAME"
kind load docker-image module3-river:0.1.0 --name "$CLUSTER_NAME"
kind load docker-image module3-state-sync:0.1.0 --name "$CLUSTER_NAME"
kind load docker-image module3-authz-shim:0.1.0 --name "$CLUSTER_NAME"

if [[ "${KIND_USE_CILIUM:-0}" == "1" ]]; then
  echo "==> install Cilium"
  helm repo add cilium https://helm.cilium.io/ >/dev/null 2>&1 || true
  helm repo update cilium >/dev/null 2>&1 || true
  helm upgrade --install cilium cilium/cilium --version 1.15.7 \
    --namespace kube-system \
    --set image.pullPolicy=IfNotPresent \
    --set ipam.mode=kubernetes \
    --set kubeProxyReplacement=true \
    --set operator.replicas=1 \
    --wait --timeout 10m
  NETWORK_POLICY_CILIUM=true
else
  echo "==> using Kind default CNI + Kubernetes NetworkPolicy (set KIND_USE_CILIUM=1 for Cilium)"
  NETWORK_POLICY_CILIUM=false
fi

kubectl wait --for=condition=Ready nodes --all --timeout=300s

echo "==> namespace + mTLS secret"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
# Annotate for Helm adoption if chart previously managed namespace
kubectl label ns "$NS" app.kubernetes.io/managed-by=Helm --overwrite >/dev/null 2>&1 || true
kubectl annotate ns "$NS" meta.helm.sh/release-name=module3-k8s meta.helm.sh/release-namespace="$NS" --overwrite >/dev/null 2>&1 || true
TMP="$(mktemp -d)"
if command -v openssl >/dev/null 2>&1; then
  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$TMP/ca.key" -out "$TMP/ca.crt" -days 365 -subj "/CN=module3-ca" >/dev/null 2>&1
  openssl req -newkey rsa:2048 -nodes -keyout "$TMP/tls.key" -out "$TMP/tls.csr" -subj "/CN=vector-db" >/dev/null 2>&1
  openssl x509 -req -in "$TMP/tls.csr" -CA "$TMP/ca.crt" -CAkey "$TMP/ca.key" -CAcreateserial -out "$TMP/tls.crt" -days 365 >/dev/null 2>&1
else
  docker run --rm -v "$TMP:/work" --entrypoint openssl alpine/openssl req -x509 -newkey rsa:2048 -nodes -keyout /work/ca.key -out /work/ca.crt -days 365 -subj "/CN=module3-ca"
  docker run --rm -v "$TMP:/work" --entrypoint openssl alpine/openssl req -newkey rsa:2048 -nodes -keyout /work/tls.key -out /work/tls.csr -subj "/CN=vector-db"
  docker run --rm -v "$TMP:/work" --entrypoint openssl alpine/openssl x509 -req -in /work/tls.csr -CA /work/ca.crt -CAkey /work/ca.key -CAcreateserial -out /work/tls.crt -days 365
fi
kubectl -n "$NS" create secret generic module3-mtls \
  --from-file=ca.crt="$TMP/ca.crt" \
  --from-file=tls.crt="$TMP/tls.crt" \
  --from-file=tls.key="$TMP/tls.key" \
  --dry-run=client -o yaml | kubectl apply -f -
rm -rf "$TMP"

echo "==> helm install module3-k8s"
helm upgrade --install module3-k8s "$CHART" \
  --namespace "$NS" \
  --set namespace="$NS" \
  --set controlUrl="$CONTROL_URL" \
  --set agentApiKey="$AGENT_API_KEY" \
  --set organizationSlug="$ORG_SLUG" \
  --set clusterName="kind-$CLUSTER_NAME" \
  --set images.agent=module3-mesh-agent:0.1.0 \
  --set images.river=module3-river:0.1.0 \
  --set images.stateSync=module3-state-sync:0.1.0 \
  --set images.authzShim=module3-authz-shim:0.1.0 \
  --set opa.enabled=true \
  --set stateSync.enabled=true \
  --set authzShim.enabled=true \
  --set llmEdge.enabled=true \
  --set networkPolicy.cilium="$NETWORK_POLICY_CILIUM" \
  --wait --timeout 5m

echo "==> Module 3 Kind stack ready (Phase 2+3)."
echo "    UI: /infrastructure/k8s-firewall  and  /infrastructure/api-governance"
echo "    Seed quotas: python scripts/module3_phase3_seed_quotas.py"
kubectl -n "$NS" get pods,svc
