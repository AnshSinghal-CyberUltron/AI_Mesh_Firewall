cd /home/ec2-user/AI_Mesh_Firewall || exit 1
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE exec -T gateway sh -c 'cd /app/gateway && python - <<'"'"'PY'"'"'
from patterns import redact_all
from scanner import InputScanner, ScanVerdict
from llm_router import _redact_text_with_backstop
PII="[user]: Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, phone 555-867-5309, credit card 4111-1111-1111-1111."
sc=InputScanner(thread_pool_size=4, config={"tier2_enabled": False})
v=ScanVerdict(action="flag", threat_type="pii", confidence=0.95, detail="test", matched_patterns=["SSN ***-**-6789"], tier="tier_2")
v.scan_meta={"findings":[{"evidence":"SSN ***-**-6789"}]}
before=PII
after=sc.redact_pii(before, verdict=v)
print("redact_all", redact_all(before)!=before)
print("redact_pii", after!=before)
print("backstop_eq_orig", _redact_text_with_backstop(before, after)==before)
PY' </dev/null
