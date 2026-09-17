"""Provision synthetic V3 A02 tenants (no secret copy from zeroshield models).

Run inside the control container:
  python /tmp/provision_synth.py

Writes /tmp/v3a02.keys.json mode 0600. Prints last4 only.
"""
from __future__ import annotations

import json
import os
import secrets
import stat

from django.contrib.auth import get_user_model
from django.db import transaction

from auth.models import Organization, Role, UserProfile
from core.models import FirewallConfig, GatewayAPIKey, LLMModelConfig

KEYS_PATH = "/tmp/v3a02.keys.json"
SYNTH = ("v3a02-block", "v3a02-observe")
FRONTEND_EMAIL = "v3a02.admin@example.invalid"


def _clone_catalog_stub(org: Organization) -> dict:
    """Register a catalog model WITHOUT copying encrypted provider keys.

    Presence in the org catalog lets /v1/chat/completions pass the
    model_not_configured gate so input scan can run. Empty encrypted_api_key
    means we do not copy zeroshield secrets.
    """
    obj, created = LLMModelConfig.objects.update_or_create(
        organization=org,
        model_name="gpt-4o-mini",
        defaults={
            "provider": "openai",
            "model_id": "openai/gpt-4o-mini",
            "api_key_env_var": "",
            "encrypted_api_key": "",
            "api_key_last4": "",
            "api_base": "",
            "is_active": True,
            "data_sensitivity_level": "public",
        },
    )
    return {
        "id": obj.id,
        "created": created,
        "model_name": obj.model_name,
        "has_encrypted_key": bool(obj.encrypted_api_key),
        "api_key_last4": obj.api_key_last4 or None,
    }


def _ensure_frontend_user(org: Organization, password: str) -> dict:
    User = get_user_model()
    user = User.objects.filter(email__iexact=FRONTEND_EMAIL).first()
    created = False
    if user is None:
        user = User.objects.create_user(
            username="v3a02admin",
            email=FRONTEND_EMAIL,
            password=password,
            is_staff=True,
            is_superuser=False,
            is_active=True,
        )
        created = True
    else:
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = False
        user.is_active = True
        user.save()
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.organization = org
    if hasattr(profile, "is_platform_operator"):
        profile.is_platform_operator = False
    profile.save()
    role, _ = Role.objects.get_or_create(
        name="org_admin",
        defaults={"description": "Organization administrator"},
    )
    profile.roles.add(role)
    FirewallConfig.load(organization=org)
    return {
        "email": FRONTEND_EMAIL,
        "user_id": user.id,
        "created": created,
        "org_slug": org.slug,
        "is_platform_operator": bool(getattr(profile, "is_platform_operator", False)),
        "is_superuser": user.is_superuser,
    }


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai_mesh_control.settings")
    payload = {"orgs": {}, "frontend": {}, "models": {}}
    frontend_password = secrets.token_urlsafe(18)
    with transaction.atomic():
        User = get_user_model()
        owner = User.objects.filter(is_superuser=True).order_by("id").first()
        for slug in SYNTH:
            org = Organization.objects.get(slug=slug)
            key, raw = GatewayAPIKey.ensure_simulator_for_org(org, owner)
            model_info = _clone_catalog_stub(org)
            payload["orgs"][slug] = {
                "org_id": org.id,
                "key_id": str(key.id),
                "key_prefix": key.prefix,
                "last4": (raw or "")[-4:] if raw else None,
                "raw": raw,
            }
            payload["models"][slug] = model_info
        block_org = Organization.objects.get(slug="v3a02-block")
        payload["frontend"] = _ensure_frontend_user(block_org, frontend_password)
        payload["frontend"]["password"] = frontend_password

    tmp = KEYS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, KEYS_PATH)
    os.chmod(KEYS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    public = {
        "orgs": {
            slug: {k: v for k, v in row.items() if k != "raw"}
            for slug, row in payload["orgs"].items()
        },
        "frontend": {k: v for k, v in payload["frontend"].items() if k != "password"},
        "models": payload["models"],
        "keys_path": KEYS_PATH,
        "mode": oct(stat.S_IMODE(os.stat(KEYS_PATH).st_mode)),
    }
    print(json.dumps(public, indent=2))


if __name__ == "__main__":
    main()
