#!/usr/bin/env bash
# Module 3 Phase 2 e2e — Kind stack + control ingest assertions.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS="${NAMESPACE:-ai-mesh-m3}"
CONTROL_URL="${CONTROL_URL:-http://127.0.0.1:8100}"
AGENT_API_KEY="${AGENT_API_KEY:?Set AGENT_API_KEY}"
ORG_SLUG="${ORG_SLUG:-zeroshield}"
EMAIL="${TEST_EMAIL:-admin@zeroshield.io}"
PASS="${TEST_PASSWORD:-Adm1n!Pass#2024}"
POLL_SEC="${POLL_SEC:-90}"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

command -v kubectl >/dev/null || fail "kubectl required"
# Prefer Python harness (works on Windows without Git Bash quirks)
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  fail "python3/python required"
fi
# Delegate assertions to Windows-friendly Python e2e when available
if [[ -f "$ROOT/scripts/module3_phase2_e2e.py" ]] && [[ "${PHASE2_E2E_NATIVE:-0}" != "1" ]]; then
  exec env CONTROL_URL="$CONTROL_URL" AGENT_API_KEY="${AGENT_API_KEY:-}" \
    ORG_SLUG="$ORG_SLUG" NAMESPACE="$NS" TEST_EMAIL="$EMAIL" TEST_PASSWORD="$PASS" \
    POLL_SEC="$POLL_SEC" "$PY" "$ROOT/scripts/module3_phase2_e2e.py"
fi

# Soft requirements for full bootstrap
if ! kubectl -n "$NS" get deploy ai-model >/dev/null 2>&1; then
  echo "Stack not installed — running module3_kind_up.sh"
  CONTROL_URL="${KIND_CONTROL_URL:-http://host.docker.internal:8100}" \
    AGENT_API_KEY="$AGENT_API_KEY" ORG_SLUG="$ORG_SLUG" \
    bash "$ROOT/scripts/module3_kind_up.sh"
fi

ok "waiting for pods"
kubectl -n "$NS" wait --for=condition=available deploy/ai-model deploy/vector-db deploy/module3-river --timeout=300s || fail "workloads not ready"
kubectl -n "$NS" rollout status ds/module3-mesh-agent --timeout=180s || true

# Network policy present (Cilium or standard)
if kubectl -n "$NS" get ciliumnetworkpolicies vector-db-allow-ai-model >/dev/null 2>&1 \
  || kubectl -n "$NS" get networkpolicies vector-db-allow-ai-model >/dev/null 2>&1; then
  ok "network policy present"
else
  fail "NetworkPolicy/CiliumNetworkPolicy missing"
fi

# Vector DB Service must not expose raw 8000
if kubectl -n "$NS" get svc vector-db -o json | python3 -c "import sys,json; ports=json.load(sys.stdin)['spec']['ports']; assert all(p.get('port')!=8000 for p in ports)"; then
  ok "localhost gauntlet: Service has no port 8000"
else
  fail "vector-db Service still exposes 8000"
fi

TOKEN=$(python3 - <<PY
import json, urllib.request
req=urllib.request.Request("$CONTROL_URL/api/auth/token/", data=json.dumps({"email":"$EMAIL","password":"$PASS"}).encode(), headers={"Content-Type":"application/json"})
print(json.load(urllib.request.urlopen(req, timeout=30))["access"])
PY
) || fail "login failed"

# Wait for topology via agent heartbeat
deadline=$((SECONDS + POLL_SEC))
found_topo=0
while (( SECONDS < deadline )); do
  TOPO=$(curl -sf -H "Authorization: Bearer $TOKEN" "$CONTROL_URL/api/module3/k8s-firewall/topology/" || true)
  if echo "$TOPO" | python3 -c "import sys,json; d=json.load(sys.stdin); raise SystemExit(0 if d.get('clusters') else 1)" 2>/dev/null; then
    found_topo=1
    break
  fi
  sleep 5
done
[[ "$found_topo" -eq 1 ]] || fail "topology empty — agent heartbeats not reaching control ($CONTROL_URL)"
ok "topology non-empty"

# Network drop events
DROPS=$(curl -sf -H "Authorization: Bearer $TOKEN" "$CONTROL_URL/api/module3/k8s-firewall/network-events/?period=24h&action=drop")
echo "$DROPS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert (d.get('results') or []), 'no drops'" || fail "no network drops"
ok "network drops present"

# Quarantine via river seed (re-push if needed)
kubectl -n "$NS" delete job -l job-name --ignore-not-found 2>/dev/null || true
kubectl -n "$NS" run river-poke --rm -i --restart=Never --image=redis:7-alpine -- \
  redis-cli -h module3-redis LPUSH module3:embeddings '{"collection":"corp-docs","anomaly_score":0.99,"force_quarantine":true,"quarantine_reason":"phase2 e2e"}' \
  >/dev/null 2>&1 || true

deadline=$((SECONDS + POLL_SEC))
found_q=0
while (( SECONDS < deadline )); do
  Q=$(curl -sf -H "Authorization: Bearer $TOKEN" "$CONTROL_URL/api/module3/k8s-firewall/embedding-queue/?period=24h&status=quarantined")
  if echo "$Q" | python3 -c "import sys,json; d=json.load(sys.stdin); raise SystemExit(0 if d.get('results') else 1)" 2>/dev/null; then
    found_q=1
    break
  fi
  sleep 5
done
[[ "$found_q" -eq 1 ]] || fail "no quarantined embeddings"
ok "river quarantine present"

# Module 2 incidents (drop or quarantine)
INC=$(curl -sf -H "Authorization: Bearer $TOKEN" "$CONTROL_URL/api/module2/incidents/?page_size=50")
echo "$INC" | python3 -c "
import sys,json
rows=(json.load(sys.stdin).get('results') or [])
titles=' '.join(str(r.get('title') or '') for r in rows)
assert ('network drop' in titles.lower()) or ('Embedding quarantined' in titles) or ('quarantined' in titles.lower()), titles[:500]
" || fail "Module 2 missing Module 3 incidents (are workers up?)"
ok "Module 2 SOC incidents present"

echo "=== Module 3 Phase 2 e2e PASSED ==="
