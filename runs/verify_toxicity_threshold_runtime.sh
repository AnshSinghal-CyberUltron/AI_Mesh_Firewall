#!/usr/bin/env bash
# Runtime proof of per-org toxicity_threshold (no singleton cross-contamination).
# ORG A=zeroshield (gpt-4), threshold->0.10  ; ORG B=acme-test (zeroshield-guard-120b), threshold=0.70
set -uo pipefail
CTRL=http://localhost:8100
GW=http://localhost:8300
A_KEY="$1"; B_KEY="$2"
A_TOK=$(curl -s -X POST "$CTRL/api/auth/token/" -H 'Content-Type: application/json' --data '{"email":"admin@zeroshield.io","password":"Adm1n!Pass#2024"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')
B_TOK=$(curl -s -X POST "$CTRL/api/auth/token/" -H 'Content-Type: application/json' --data '{"email":"client2@acme.test","password":"Acme!Pass#2024"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')

echo "== Set ORG A toxicity_threshold=0.10, ensure ORG B=0.70 =="
printf '{"toxicity_threshold":0.10}' > /tmp/txA.json
curl -s -X PUT "$CTRL/api/firewall/config/" -H "Authorization: Bearer $A_TOK" -H 'Content-Type: application/json' --data @/tmp/txA.json | python3 -c 'import sys,json;print("  ORG A toxicity_threshold =",json.load(sys.stdin).get("toxicity_threshold"))'
printf '{"toxicity_threshold":0.70}' > /tmp/txB.json
curl -s -X PUT "$CTRL/api/firewall/config/" -H "Authorization: Bearer $B_TOK" -H 'Content-Type: application/json' --data @/tmp/txB.json | python3 -c 'import sys,json;print("  ORG B toxicity_threshold =",json.load(sys.stdin).get("toxicity_threshold"))'
sleep 3

PROMPT='you are stupid'
echo "== ORG A (threshold 0.10) sends single-toxic-word prompt (expect tier-1 toxicity BLOCK threshold=0.10) =="
curl -s -o /dev/null -w '  ORG A http=%{http_code}\n' -X POST "$GW/v1/chat/completions" -H "Authorization: Bearer $A_KEY" -H 'Content-Type: application/json' --data "{\"model\":\"gpt-4\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}]}"
sleep 2
echo "== ORG B (threshold 0.70) sends SAME prompt (expect NO tier-1 toxicity block; threshold=0.70) =="
curl -s -o /dev/null -w '  ORG B http=%{http_code}\n' -X POST "$GW/v1/chat/completions" -H "Authorization: Bearer $B_KEY" -H 'Content-Type: application/json' --data "{\"model\":\"zeroshield-guard-120b\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}]}"
sleep 2
echo "== Tier-1 toxicity logs (note differing thresholds per org) =="
docker compose logs gateway --since 18s 2>&1 | grep -iE "Toxic content detected|threat_type=.?toxicity|Input blocked by scanner \(type=toxicity" | tail -8

echo "== Revert ORG A toxicity_threshold=0.70 =="
printf '{"toxicity_threshold":0.70}' > /tmp/txA2.json
curl -s -X PUT "$CTRL/api/firewall/config/" -H "Authorization: Bearer $A_TOK" -H 'Content-Type: application/json' --data @/tmp/txA2.json | python3 -c 'import sys,json;print("  reverted ORG A toxicity_threshold =",json.load(sys.stdin).get("toxicity_threshold"))'
