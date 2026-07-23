cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE exec -T gateway sh -c "cd /app/gateway && python - <<'PY'
from ai_mesh_gateway.patterns import redact_all
from ai_mesh_gateway.scanner import InputScanner
from ai_mesh_gateway.llm_router import _redact_text_with_backstop

PII='Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111.'
r=redact_all(PII)
print('redact_all_changed', r!=PII)
print(r[:200])
sc=InputScanner({'tier2_enabled': False})
class V:
    matched_patterns=['SSN ***-**-6789','email j***@a***.com']
    scan_meta={'findings':[{'evidence':'SSN ***-**-6789'},{'evidence':'4111-1111-1111-1111'}]}
v=V()
rp=sc.redact_pii(PII, verdict=v)
print('redact_pii_changed', rp!=PII)
print('backstop_noop', _redact_text_with_backstop(PII, rp)==PII)
PY" </dev/null
