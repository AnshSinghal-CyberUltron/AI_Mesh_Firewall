cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gwfix.yml"
$COMPOSE exec -T gateway sh -c 'python - <<PY
import asyncio
from ai_mesh_gateway.main import _extract_prompt_from_messages
from ai_mesh_gateway.scanner import InputScanner
import ai_mesh_gateway.main as main_mod

raw="Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111."
pref=_extract_prompt_from_messages([{"role":"user","content":raw}])
sc=InputScanner(thread_pool_size=4, config={"tier2_enabled": True})
main_mod.INPUT_SCANNER=sc

async def run():
    t1=await sc.scan_prompt(pref)
    print("tier1", t1.action, t1.threat_type)
    v=await sc.scan_prompt_with_tier2(pref, org_slug="zeroshield", request_id="sim2")
    print("combined", v.action, v.threat_type, (v.scan_meta or {}).get("recommended_action"))
asyncio.run(run())
PY' </dev/null
