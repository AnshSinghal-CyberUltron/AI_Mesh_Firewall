cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gwfix.yml"
$COMPOSE exec -T gateway sh -c 'python - <<PY
import asyncio
from ai_mesh_gateway.main import _extract_prompt_from_messages, _apply_input_pii_redaction, INPUT_SCANNER
from ai_mesh_gateway.patterns import detect_pii

raw="Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111."
pref=_extract_prompt_from_messages([{"role":"user","content":raw}])

async def run():
    t1=await INPUT_SCANNER.scan_prompt(pref)
    print("tier1 only", t1.action, t1.threat_type, t1.tier)
    v=await INPUT_SCANNER.scan_prompt_with_tier2(pref, org_slug="zeroshield", request_id="sim1")
    print("tier2 combined", v.action, v.threat_type, v.tier, v.detail[:80])
    before=pref
    after=_apply_input_pii_redaction(before, v)
    print("redact changed", after!=before, "raw left", bool(detect_pii(after)))
asyncio.run(run())
PY' </dev/null
