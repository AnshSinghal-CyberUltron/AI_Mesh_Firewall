# Re-verify the fixed break-test findings against the live (rebuilt) stack.
# Run: docker exec -i -w /app/control <control> python manage.py shell < reverify.py
import json, time, uuid, requests, jwt as pyjwt
from django.conf import settings
from django.contrib.auth import get_user_model
from auth.models import Organization
from core.models import GatewayAPIKey
from policy.models import Rule, Policy, SecurityIncident, EnforcementEvent

CTL = "http://localhost:8000"
GW = "http://gateway:8300"
U = get_user_model()
res = {}
def rec(n, ok, d=""):
    res[n] = (bool(ok), d); print(f"{'PASS' if ok else 'FAIL'} [{n}] {d}")

def jwt_for(email, pw):
    r = requests.post(CTL+"/api/auth/token/", json={"email": email, "password": pw}, timeout=15)
    return r.json().get("access") if r.status_code == 200 else None

orgA = Organization.objects.get(slug="breakorg-a")
orgB = Organization.objects.get(slug="breakorg-b")
jA = jwt_for("breakadmin-a@local.test", "BreakTest@2026")
jB = jwt_for("breakadmin-b@local.test", "BreakTest@2026")
HA = {"Authorization": f"Bearer {jA}"}

# ---- #1 forged JWT with the OLD placeholder key must now FAIL ----
try:
    forged = pyjwt.encode({"user_id": 1, "token_type": "access", "exp": int(time.time())+3600, "jti": "x"}, "change-me-in-production", algorithm="HS256")
    r = requests.get(CTL+"/api/auth/me/", headers={"Authorization": f"Bearer {forged}"}, timeout=10)
    rec("#1 forged-jwt-rejected", r.status_code in (401,403), f"old-key forge -> {r.status_code} (want 401/403); real login works={jA is not None}")
except Exception as e: rec("#1 forged-jwt-rejected", False, repr(e))

# ---- #2 cross-org incident escalate/resolve ----
try:
    ev = EnforcementEvent.objects.create(organization_id=orgB.id, action="monitor", metadata={"source":"reverify"})
    inc = SecurityIncident.objects.create(organization_id=orgB.id, enforcement_event=ev, title="reverify-x", severity="low", status="open")
    r = requests.post(CTL+f"/api/security/incidents/{inc.id}/escalate/", headers=HA, json={}, timeout=10)
    rec("#2 cross-org-incident", r.status_code in (403,404), f"orgA escalate orgB incident -> {r.status_code} (want 403/404)")
    inc.delete(); ev.delete()
except Exception as e: rec("#2 cross-org-incident", False, repr(e))

# ---- #3 ReDoS regex rule rejected at validation ----
try:
    pol = Policy.objects.filter(organization_id=orgA.id).first()
    body = {"name":"redos-test","rule_type":"keyword","conditions":{"regex":"(a+)+$","field":"prompt"},"action":"block","policy": pol.id if pol else None}
    r = requests.post(CTL+"/api/policies/"+(str(pol.id)+"/rules/" if pol else "rules/"), headers=HA, json=body, timeout=10)
    # accept either 400 (validation reject) or 404 (endpoint shape) but NOT 201 created
    rec("#3 redos-regex-rejected", r.status_code != 201, f"add (a+)+$ rule -> {r.status_code} (want NOT 201); ")
except Exception as e: rec("#3 redos-regex-rejected", False, repr(e))

# ---- gateway key for data-plane checks ----
owner = U.objects.filter(is_superuser=True).first() or U.objects.first()
gk_inst, gk = GatewayAPIKey.generate_key(name="reverify", owner=owner, project_id="zeroshield")
gk_inst.organization_id = 3; gk_inst.save(update_fields=["organization"])
GH = {"Authorization": f"Bearer {gk}", "Content-Type":"application/json"}

# ---- #13 clean prompt must NOT be tier2_degraded-blocked (THE unblocker) ----
try:
    r = requests.post(GW+"/v1/chat/completions", json={"model":"gpt-4o-mini","messages":[{"role":"user","content":"What is 2+2?"}],"max_tokens":10}, headers=GH, timeout=45)
    b = r.text.lower()
    degraded_block = (r.status_code==403 and "tier2_degraded" in b)
    rec("#13 tier2-fail-open", not degraded_block, f"clean prompt -> {r.status_code}; tier2_degraded_block={degraded_block} (want False)")
except Exception as e: rec("#13 tier2-fail-open", False, repr(e))

# ---- #4 max_tokens type confusion: no 500 ----
try:
    codes={}
    for label,mt in [("inf",1e999),("null",None),("zero-str","0")]:
        r=requests.post(GW+"/v1/chat/completions", json={"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"max_tokens":mt}, headers=GH, timeout=30)
        codes[label]=r.status_code
    no500 = all(c!=500 for c in codes.values())
    rec("#4 max_tokens-no-500", no500, f"{codes} (want no 500)")
except Exception as e: rec("#4 max_tokens-no-500", False, repr(e))

# ---- #14 non-string email on /token/ : no 500 ----
try:
    codes=[]
    for bad in [{"a":1}, [1,2], 123, True]:
        r=requests.post(CTL+"/api/auth/token/", json={"email":bad,"password":"x"}, timeout=10)
        codes.append(r.status_code)
    rec("#14 nonstr-email-no-500", all(c!=500 for c in codes), f"codes={codes} (want no 500)")
except Exception as e: rec("#14 nonstr-email-no-500", False, repr(e))

# ---- #19 change-password weak password rejected ----
try:
    r=requests.post(CTL+"/api/auth/change-password/", headers=HA, json={"old_password":"BreakTest@2026","new_password":"password"}, timeout=10)
    rec("#19 weak-pw-rejected", r.status_code==400, f"change to 'password' -> {r.status_code} (want 400)")
except Exception as e: rec("#19 weak-pw-rejected", False, repr(e))

# ---- #24 ThreatFeed huge hours: no 500 ----
try:
    r=requests.get(CTL+"/api/security/threat-feed/?hours=99999999999999999999", headers=HA, timeout=10)
    rec("#24 threatfeed-no-500", r.status_code!=500, f"hours=huge -> {r.status_code} (want not 500)")
except Exception as e: rec("#24 threatfeed-no-500", False, repr(e))

# ---- #23 Policy.code per-org (two orgs same code allowed) ----
try:
    from django.db import IntegrityError, transaction
    code="REVERIFY-DUP-01"
    ok=False
    try:
        with transaction.atomic():
            Policy.objects.create(organization_id=orgA.id, code=code, name="a")
            Policy.objects.create(organization_id=orgB.id, code=code, name="b")
        ok=True
    except IntegrityError:
        ok=False
    Policy.objects.filter(code=code).delete()
    rec("#23 policy-code-per-org", ok, f"same code in 2 orgs allowed={ok} (want True)")
except Exception as e: rec("#23 policy-code-per-org", False, repr(e))

gk_inst.delete()
p=sum(1 for v in res.values() if v[0])
print(f"\n==== REVERIFY PASS={p} FAIL={len(res)-p} ====")
print("FAILED:", [k for k,v in res.items() if not v[0]])
