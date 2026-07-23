cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gwfix.yml"
$COMPOSE exec -T gateway sh -c 'python - <<PY
from ai_mesh_gateway.main import _apply_input_pii_redaction, _extract_prompt_from_messages
from ai_mesh_gateway.scanner import ScanVerdict, InputScanner
import asyncio

raw="Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111."
prefixed=_extract_prompt_from_messages([{"role":"user","content":raw}])
print("prefixed=", prefixed[:120])
v=ScanVerdict(action="flag", threat_type="pii", confidence=0.95, detail="t", matched_patterns=[], tier="tier_2")
v.scan_meta={"recommended_action":"redact","findings":[{"evidence":"SSN","category":"pii"}]}
out=_apply_input_pii_redaction(prefixed,v)
print("changed", out!=prefixed)
print("out=", out[:160])

sc=InputScanner(thread_pool_size=2, config={"tier2_enabled": True})
async def run():
    verdict=await sc.scan_prompt_with_tier2(prefixed, org_slug="zeroshield", request_id="dbg")
    print("scan action", verdict.action, "type", verdict.threat_type)
    red=_apply_input_pii_redaction(prefixed, verdict)
    print("after scan redact changed", red!=prefixed)
    print(red[:160])
asyncio.run(run())
PY' </dev/null
