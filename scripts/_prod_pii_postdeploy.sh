cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gwfix.yml"
KEY=$(grep -E '^DEMO_GATEWAY_KEY=' .env | head -1 | cut -d= -f2-)
GW=http://127.0.0.1:8300/v1/chat/completions

cat > /tmp/pii_probe.json <<'JSON'
{"model":"auto","messages":[{"role":"user","content":"Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111."}]}
JSON

echo "=== PII probe ==="
code=$(curl -s -o /tmp/pii_out.json -w '%{http_code}' -X POST "$GW" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d @/tmp/pii_probe.json)
echo "HTTP $code"
cat /tmp/pii_out.json; echo
RID=$(python3 -c "import json; print(json.load(open('/tmp/pii_out.json')).get('request_id',''))" 2>/dev/null || true)
echo "request_id=$RID"
sleep 2
echo "=== gateway logs for request ==="
$COMPOSE logs gateway --tail 120 2>/dev/null | grep -E "$RID|no-op|redact|input_scan|PII" | tail -40

echo "=== in-container redaction sanity ==="
$COMPOSE exec -T gateway sh -c 'python - <<PY
from ai_mesh_gateway.main import _apply_input_pii_redaction
from ai_mesh_gateway.scanner import ScanVerdict
prompt="Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111."
v=ScanVerdict(action="flag", threat_type="pii", confidence=0.95, detail="t", matched_patterns=[], tier="tier_2")
v.scan_meta={"recommended_action":"redact","findings":[{"evidence":"SSN","category":"pii"}]}
out=_apply_input_pii_redaction(prompt,v)
print("changed", out!=prompt)
print(out[:200])
PY' </dev/null || true
