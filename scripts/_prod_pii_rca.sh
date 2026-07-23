cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
KEY=$(grep -E '^DEMO_GATEWAY_KEY=' .env | head -1 | cut -d= -f2-)
PII='Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111.'
GW=http://127.0.0.1:8300/v1/chat/completions
echo "=== PII probe (expect redact+allow after fix) ==="
RID=$(curl -s -D /tmp/pii.hdr -o /tmp/pii.json -X POST "$GW" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"$PII\"}]}")
HTTP=$(grep -i '^HTTP' /tmp/pii.hdr | tail -1 | awk '{print $2}')
echo "HTTP $HTTP"
grep -i x-request-id /tmp/pii.hdr | tail -1
head -c 500 /tmp/pii.json; echo
RID=$(grep -i x-request-id /tmp/pii.hdr | tail -1 | tr -d '\r' | awk '{print $2}')
echo "request_id=$RID"
echo "=== gateway log lines for request ==="
$COMPOSE logs --since 5m gateway </dev/null 2>&1 | grep -F "$RID" | tail -20
echo "=== recent PII/redact/no-op lines ==="
$COMPOSE logs --since 5m gateway </dev/null 2>&1 | grep -Ei 'PII/secret|redact|no-op|unmaskable|input_blocked|score_threshold|model_recommended' | tail -25
