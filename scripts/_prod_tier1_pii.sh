cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.gwfix.yml"
$COMPOSE exec -T gateway sh -c 'python - <<PY
from ai_mesh_gateway.main import _extract_prompt_from_messages
from ai_mesh_gateway.patterns import detect_pii, redact_all
raw="Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111."
pref=_extract_prompt_from_messages([{"role":"user","content":raw}])
print("detect_pii raw", detect_pii(raw))
print("detect_pii prefixed", detect_pii(pref))
print("redact_all changed", redact_all(pref)!=pref)
print(redact_all(pref)[:160])
PY' </dev/null
