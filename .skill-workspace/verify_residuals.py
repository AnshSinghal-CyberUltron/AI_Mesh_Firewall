# Run inside control: docker exec -i -w /app/control <control> python manage.py shell < verify_residuals.py
import json, time, requests
from django.conf import settings
from django.contrib.auth import get_user_model
from auth.models import Organization
from core.models import GatewayAPIKey, OrganizationAgentKey
from core.signals import resync_all_gateway_keys
from policy.models import EnforcementEvent

B = "http://localhost:8000"
results = {}

def rec(name, ok, detail=""):
    results[name] = (bool(ok), detail)
    print(f"{'PASS' if ok else 'FAIL'} [{name}] {detail}")

# ---------- G: DEBUG=False ----------
try:
    rec("G:debug_false", settings.DEBUG is False, f"settings.DEBUG={settings.DEBUG}")
except Exception as e:
    rec("G:debug_false", False, repr(e))

# A 404 under DEBUG=False must NOT render the Django debug 404 (which lists the URLconf).
try:
    r = requests.get(B + "/api/this-route-does-not-exist-zzz/", timeout=10)
    body = r.text.lower()
    leaky = ("urlconf" in body) or ("using the urlconf defined" in body) or ("djangoproject" in body and "traceback" in body)
    rec("G:no_debug_404", (not leaky), f"status={r.status_code} leaky={leaky}")
except Exception as e:
    rec("G:no_debug_404", False, repr(e))

# ---------- B: gateway-key Redis resync ----------
try:
    import redis as _redis
    rc = _redis.from_url(settings.REDIS_URL, decode_responses=True)
    gk = GatewayAPIKey.objects.filter(is_active=True).first()
    assert gk is not None, "no active GatewayAPIKey"
    rkey = f"auth:apikey:{gk.key_hash}"
    before = rc.exists(rkey)
    rc.delete(rkey)                      # simulate a Redis flush/eviction
    deleted_gone = (rc.exists(rkey) == 0)
    n = resync_all_gateway_keys()        # the loop body the control thread runs periodically
    restored = (rc.exists(rkey) == 1)
    rec("B:resync_restores_key", deleted_gone and restored, f"before={before} reconciled={n} restored={restored}")
except Exception as e:
    rec("B:resync_restores_key", False, repr(e))

# ---------- F: ingestion -> telemetry:events -> EnforcementEvent ----------
try:
    org = Organization.objects.order_by("id").first()
    assert org is not None, "no org"
    inst, raw = OrganizationAgentKey.generate_key(org, name="verify-ingestion-key")
    H = {"Authorization": f"Bearer {raw}"}
    ev = lambda: {"agent_id": "verify-agent", "timestamp": "2026-06-12T00:00:00Z",
                  "event_type": "request", "data": {"action": "allow", "model": "test"}}
    base = EnforcementEvent.objects.filter(organization_id=org.id, metadata__source="ingestion_api").count()
    r1 = requests.post(B + "/api/ingestion/events/", json=ev(), headers=H, timeout=15)
    batch = {"events": [ev() for _ in range(5)]}
    r2 = requests.post(B + "/api/ingestion/events/batch/", json=batch, headers=H, timeout=15)
    # cap still fires behind valid auth
    big = {"events": [ev() for _ in range(1001)]}
    r3 = requests.post(B + "/api/ingestion/events/batch/", json=big, headers=H, timeout=20)
    # let the 1s control drain persist
    persisted = base
    for _ in range(20):
        time.sleep(1.0)
        persisted = EnforcementEvent.objects.filter(organization_id=org.id, metadata__source="ingestion_api").count()
        if persisted >= base + 6:
            break
    ok = (r1.status_code == 202 and r1.json().get("persisted") is True
          and r2.status_code == 202 and r3.status_code == 400
          and persisted >= base + 6)
    rec("F:ingestion_persists", ok,
        f"single={r1.status_code}/{r1.json().get('persisted')} batch={r2.status_code} cap1001={r3.status_code} "
        f"persisted {base}->{persisted}")
    inst.delete()
except Exception as e:
    rec("F:ingestion_persists", False, repr(e))

# ---------- D: vector bundle re-keyed by organization_id ----------
try:
    from policy.vector_compiler import VectorPolicyCompiler, REDIS_KEY_COMPILED
    import redis as _redis
    rc = _redis.from_url(settings.REDIS_URL, decode_responses=True)
    VectorPolicyCompiler().compile_and_push(trigger="verify")
    bundle = json.loads(rc.get(REDIS_KEY_COMPILED) or "{}")
    keys = list(bundle.get("policies", {}).keys())
    # every key prefix must now be an integer org id (collision-free tenant key)
    all_org_keyed = bool(keys) and all(k.split("::", 1)[0].isdigit() for k in keys)
    sample = bundle.get("policies", {}).get(keys[0]) if keys else {}
    has_org_in_payload = "organization_id" in (sample or {})
    rec("D:bundle_org_keyed", all_org_keyed and has_org_in_payload,
        f"count={len(keys)} sample_keys={keys[:3]} payload_has_org={has_org_in_payload}")
except Exception as e:
    rec("D:bundle_org_keyed", False, repr(e))

passed = sum(1 for v in results.values() if v[0])
print(f"\n==== CONTROL SUMMARY PASS={passed} FAIL={len(results)-passed} ====")
print("FAILED:", [k for k, v in results.items() if not v[0]])
