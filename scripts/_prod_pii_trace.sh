cd /home/ec2-user/AI_Mesh_Firewall || exit 1
KEY=$(grep -E '^DEMO_GATEWAY_KEY=' .env | head -1 | cut -d= -f2-)
PII='Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111.'
curl -s -X POST http://127.0.0.1:8300/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"$PII\"}]}" \
  > /tmp/pii_trace.json
python3 - <<'PY'
import json
obj=json.load(open('/tmp/pii_trace.json'))
trace=obj.get('pipeline_trace') or {}
for st in trace.get('stages') or []:
    if st.get('name')=='input_scan':
        print('input_scan action', st.get('action'))
        print('detail', (st.get('detail') or '')[:500])
        print('guard', (st.get('guard_reason') or '')[:800])
print('category', obj.get('category'))
print('blocked_by', obj.get('blocked_by'))
print('code', obj.get('code'))
PY
