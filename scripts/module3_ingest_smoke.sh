#!/usr/bin/env bash
# Module 3 ingest smoke test — requires control plane on :8100 and AGENT_API_KEY set.
set -euo pipefail

BASE_URL="${CONTROL_URL:-http://127.0.0.1:8100}"
AGENT_KEY="${AGENT_API_KEY:-}"

if [[ -z "$AGENT_KEY" ]]; then
  echo "AGENT_API_KEY not set — ingest endpoints require agent auth when configured."
  echo "Set AGENT_API_KEY in .env or export it for this script."
  exit 1
fi

auth_header="Authorization: Bearer ${AGENT_KEY}"

echo "==> cluster heartbeat"
curl -sf -X POST "${BASE_URL}/api/module3/ingest/cluster-heartbeat/" \
  -H "${auth_header}" \
  -H "Content-Type: application/json" \
  -d '{
    "cluster_name": "smoke-cluster",
    "k8s_version": "1.28",
    "cilium_enabled": true,
    "pods": [
      {
        "namespace": "ai-models",
        "pod_name": "smoke-pod-0",
        "workload_type": "model",
        "sidecar_attached": true,
        "mtls_status": "healthy"
      }
    ]
  }'
echo ""

echo "==> network event"
curl -sf -X POST "${BASE_URL}/api/module3/ingest/network-event/" \
  -H "${auth_header}" \
  -H "Content-Type: application/json" \
  -d '{
    "cluster_name": "smoke-cluster",
    "layer": "ebpf",
    "action": "drop",
    "source_ref": "smoke/compromised",
    "dest_ref": "vector-db/chroma-0",
    "reason": "smoke test drop"
  }'
echo ""

echo "==> embedding inspection"
curl -sf -X POST "${BASE_URL}/api/module3/ingest/embedding-inspection/" \
  -H "${auth_header}" \
  -H "Content-Type: application/json" \
  -d '{
    "collection": "smoke-docs",
    "status": "clean",
    "anomaly_score": 0.05
  }'
echo ""

echo "Module 3 ingest smoke test passed."
