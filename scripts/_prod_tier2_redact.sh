cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE exec -T gateway sh -c "cd /app/gateway && python - <<'PY'
import asyncio
from scanner import InputScanner
from llm_router import _redact_text_with_backstop

PII='Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111.'
sc=InputScanner(thread_pool_size=4, config={'tier2_enabled': True})

async def main():
    v=await sc.scan_prompt_with_tier2(PII, org_slug='zeroshield', request_id='probe')
    print('verdict action', v.action, 'threat', v.threat_type, 'tier', v.tier)
    print('recommended', (v.scan_meta or {}).get('recommended_action'))
    before=PII
    after=sc.redact_pii(before, verdict=v)
    print('redact_changed', after!=before)
    print('after_head', after[:200])
    print('backstop_eq_orig', _redact_text_with_backstop(before, after)==before)

asyncio.run(main())
PY" </dev/null
