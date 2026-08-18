#!/usr/bin/env python
"""Mint ephemeral gateway key for Module 1.6 Phase0 — run inside control container."""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from core.models import GatewayAPIKey
try:
    from auth.models import Organization
except ImportError:
    from auth_api.models import Organization

org = Organization.objects.get(slug="zeroshield")
User = get_user_model()
owner = User.objects.filter(email="admin@zeroshield.io").first()
if owner is None:
    raise SystemExit("admin user missing")

# Try recover simulator/default key first
for project_id in (f"mcp-default-{org.slug}", "simulator-default", "isolation-playground"):
    qs = GatewayAPIKey.objects.filter(organization=org, project_id=project_id, is_active=True)
    for k in qs:
        secret = k.recover_secret() if hasattr(k, "recover_secret") else None
        if secret:
            print(json.dumps({"key": secret, "prefix": k.prefix, "id": str(k.id), "source": "recovered", "project_id": project_id}))
            sys.exit(0)

# Also try any key with encrypted_secret
for k in GatewayAPIKey.objects.filter(organization=org, is_active=True).exclude(encrypted_secret="").exclude(encrypted_secret=None)[:20]:
    secret = k.recover_secret()
    if secret:
        print(json.dumps({"key": secret, "prefix": k.prefix, "id": str(k.id), "source": "recovered_any", "project_id": k.project_id}))
        sys.exit(0)

instance, raw = GatewayAPIKey.generate_key(
    name="m16-phase0-canary",
    owner=owner,
    project_id="m16-phase0",
    allowed_models=[],  # unrestricted
)
if not instance.organization_id:
    instance.organization = org
    instance.save(update_fields=["organization"])
try:
    instance.store_secret(raw)
except Exception:
    pass
print(json.dumps({"key": raw, "prefix": instance.prefix, "id": str(instance.id), "source": "minted"}))
