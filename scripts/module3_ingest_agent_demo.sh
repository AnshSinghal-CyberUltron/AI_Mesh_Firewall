#!/usr/bin/env bash
# One-shot Module 3 ingest agent demo — posts live telemetry so M3.2 UI fills without seed_module3.
# Requires: AGENT_API_KEY (global or OrganizationAgentKey) and control on CONTROL_URL.
# When using the global AGENT_API_KEY, set ORG_SLUG (or ORGANIZATION_ID) so events attach to an org.
#
# Usage:
#   export AGENT_API_KEY=...
#   export ORG_SLUG=zeroshield
#   bash scripts/module3_ingest_agent_demo.sh
set -euo pipefail

BASE_URL="${CONTROL_URL:-http://127.0.0.1:8100}"
AGENT_KEY="${AGENT_API_KEY:-}"
CLUSTER_NAME="${CLUSTER_NAME:-agent-demo-cluster}"
LOOP_SEC="${LOOP_SEC:-0}"
ORG_SLUG="${ORG_SLUG:-zeroshield}"
ORG_ID="${ORGANIZATION_ID:-}"

if [[ -z "$AGENT_KEY" ]]; then
  echo "AGENT_API_KEY not set." >&2
  exit 1
fi

auth_header="Authorization: Bearer ${AGENT_KEY}"

org_json_fields() {
  if [[ -n "$ORG_ID" ]]; then
    printf '"organization_id": %s,' "$ORG_ID"
  elif [[ -n "$ORG_SLUG" ]]; then
    printf '"organization_slug": "%s",' "$ORG_SLUG"
  else
    printf ''
  fi
}

post_once() {
  local ts org_fields payload_hash
  ts="$(date +%s)"
  org_fields="$(org_json_fields)"
  payload_hash="$(python3 -c "import hashlib; print(hashlib.sha256(b'poison-${ts}').hexdigest())")"

  echo "==> cluster heartbeat ($CLUSTER_NAME)"
  curl -sf -X POST "${BASE_URL}/api/module3/ingest/cluster-heartbeat/" \
    -H "${auth_header}" \
    -H "Content-Type: application/json" \
    -d "{
      ${org_fields}
      \"cluster_name\": \"${CLUSTER_NAME}\",
      \"k8s_version\": \"1.28\",
      \"cilium_enabled\": true,
      \"status\": \"healthy\",
      \"pods\": [
        {
          \"namespace\": \"ai-models\",
          \"pod_name\": \"llm-infer-${ts}\",
          \"workload_type\": \"model\",
          \"sidecar_attached\": true,
          \"mtls_status\": \"healthy\"
        },
        {
          \"namespace\": \"vector-db\",
          \"pod_name\": \"chroma-0\",
          \"workload_type\": \"vector-db\",
          \"sidecar_attached\": true,
          \"mtls_status\": \"healthy\"
        }
      ]
    }"
  echo ""

  echo "==> network drop"
  curl -sf -X POST "${BASE_URL}/api/module3/ingest/network-event/" \
    -H "${auth_header}" \
    -H "Content-Type: application/json" \
    -d "{
      ${org_fields}
      \"cluster_name\": \"${CLUSTER_NAME}\",
      \"layer\": \"ebpf\",
      \"action\": \"drop\",
      \"source_ref\": \"compromised/agent-${ts}\",
      \"dest_ref\": \"vector-db/chroma-0\",
      \"reason\": \"agent demo: unauthorized sender label\"
    }"
  echo ""

  echo "==> embedding quarantined"
  curl -sf -X POST "${BASE_URL}/api/module3/ingest/embedding-inspection/" \
    -H "${auth_header}" \
    -H "Content-Type: application/json" \
    -d "{
      ${org_fields}
      \"collection\": \"corp-docs\",
      \"status\": \"quarantined\",
      \"anomaly_score\": 0.97,
      \"payload_hash\": \"${payload_hash}\",
      \"quarantine_reason\": \"agent demo: embedding poisoning suspected\"
    }"
  echo ""
}

post_once
echo "Module 3 ingest agent demo posted. Refresh M3.2 K8s Firewall UI."
echo "Network drop + quarantine should open Module 2 incidents (Celery workers required)."

if [[ "${LOOP_SEC}" =~ ^[1-9][0-9]*$ ]]; then
  echo "Looping every ${LOOP_SEC}s (Ctrl+C to stop)…"
  while true; do
    sleep "$LOOP_SEC"
    post_once
  done
fi
