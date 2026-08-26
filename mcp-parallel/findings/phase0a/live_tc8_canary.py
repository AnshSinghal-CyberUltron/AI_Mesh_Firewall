"""Live T-C8 canary: superuser-no-org must not see a foreign-tenant secret.

Creates a throwaway superuser (org=None) and a canary EnforcementEvent, hits
gunicorn /api/security/soc-kpis/, then deletes both. Run inside the control
container so ORM and HTTP share the live DB; HTTP goes to 127.0.0.1:8000 (the
reloaded workers).
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.request

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from auth.models import Organization, UserProfile  # noqa: E402
from policy.constants import ACTION_BLOCK  # noqa: E402
from policy.models import EnforcementEvent  # noqa: E402
from policy.security_views import _enforcement_events_for_request  # noqa: E402
from django.test import RequestFactory  # noqa: E402

User = get_user_model()
CANARY = "phase0a-tc8-" + secrets.token_hex(8)
EMAIL = f"phase0a-tc8-{secrets.token_hex(4)}@example.invalid"
PASSWORD = secrets.token_urlsafe(24)
user = None
event = None
ok = False
try:
    org_b = Organization.objects.exclude(slug="zeroshield").order_by("id").first()
    if org_b is None:
        org_b = Organization.objects.order_by("id").first()
    event = EnforcementEvent.objects.create(
        organization=org_b,
        action=ACTION_BLOCK,
        metadata={"canary": CANARY},
    )
    user = User.objects.create_superuser(username=EMAIL.split("@")[0], email=EMAIL, password=PASSWORD)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.organization = None
    profile.save(update_fields=["organization"])

    req = RequestFactory().get("/api/security/soc-kpis/")
    req.user = user
    qs = _enforcement_events_for_request(req)
    helper_ids = list(qs.values_list("id", flat=True))
    helper_clean = event.id not in helper_ids and len(helper_ids) == 0

    payload = json.dumps({"email": EMAIL, "password": PASSWORD}).encode()
    token_req = urllib.request.Request(
        "http://127.0.0.1:8000/api/auth/token/",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(token_req, timeout=15) as resp:
        tokens = json.loads(resp.read().decode())
    access = tokens.get("access")
    kpis_req = urllib.request.Request(
        "http://127.0.0.1:8000/api/security/soc-kpis/?period=24h",
        headers={"Authorization": f"Bearer {access}"},
    )
    with urllib.request.urlopen(kpis_req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    blob = json.dumps(body)
    api_clean = CANARY not in blob and int(body.get("total_threats") or 0) == 0
    ok = helper_clean and api_clean
    print(
        json.dumps(
            {
                "ok": ok,
                "helper_clean": helper_clean,
                "api_clean": api_clean,
                "helper_count": len(helper_ids),
                "total_threats": body.get("total_threats"),
                "canary_in_body": CANARY in blob,
            }
        )
    )
    sys.exit(0 if ok else 1)
finally:
    if event is not None:
        EnforcementEvent.objects.filter(pk=event.pk).delete()
    if user is not None:
        User.objects.filter(pk=user.pk).delete()
    if not ok:
        print("T-C8 FAIL (cleanup done)", file=sys.stderr)
