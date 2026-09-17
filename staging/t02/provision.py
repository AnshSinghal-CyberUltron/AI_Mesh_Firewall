"""Provision T02 synth tenants + recorder BYOK. Run inside control.

  python /tmp/provision_t02.py

Writes /tmp/t02.keys.json mode 0600. Prints last4 only. Never prints full keys.
Requires env T02_RECORDER_KEY (same value as the recorder container).
"""
from __future__ import annotations

import json
import os
import secrets
import stat

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "main_app.settings"))
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction

from auth.models import Organization, Role, UserProfile
from core.models import FirewallConfig, GatewayAPIKey, LLMModelConfig

KEYS_PATH = "/tmp/t02.keys.json"
SYNTH = ("v3a02-block", "v3a02-observe")
FRONTEND_EMAIL = "v3a02.admin@example.invalid"
RECORDER_BASE = os.environ.get("T02_RECORDER_API_BASE", "http://t02-recorder:8080/v1").strip()
RECORDER_KEY = os.environ.get("T02_RECORDER_KEY", "").strip()
MODEL_NAME = "gpt-4o-mini"


def _ensure_org(slug: str, name: str) -> Organization:
    org, _ = Organization.objects.get_or_create(
        slug=slug,
        defaults={"name": name, "is_active": True},
    )
    if not org.is_active:
        org.is_active = True
        org.save(update_fields=["is_active"])
    FirewallConfig.load(organization=org)
    return org


def _configure_firewall(org: Organization, *, mode: str, pii: bool) -> None:
    cfg = FirewallConfig.load(organization=org)
    cfg.enforcement_mode = mode
    cfg.pii_detection_enabled = pii
    cfg.content_filtering_enabled = True
    cfg.firewall_enabled = True
    cfg.blocked_keywords = "T02BLOCKCANARY" if mode == "block" else ""
    cfg.requests_per_minute = 10000
    cfg.burst_limit = 1000
    cfg.save()


def _ensure_recorder_model(org: Organization) -> dict:
    if not RECORDER_KEY:
        raise SystemExit("T02_RECORDER_KEY is required")
    obj, created = LLMModelConfig.objects.update_or_create(
        organization=org,
        model_name=MODEL_NAME,
        defaults={
            "provider": "custom",
            "model_id": "gpt-4o-mini",
            "api_key_env_var": "",
            "api_base": RECORDER_BASE,
            "is_active": True,
            "data_sensitivity_level": "public",
            "cost_per_1k_input_tokens": "0.001",
            "cost_per_1k_output_tokens": "0.002",
            "latency_sla_ms": 8000,
            "routing_priority": 10,
            "risk_score": 0.2,
        },
    )
    obj.provider = "custom"
    obj.model_id = "gpt-4o-mini"
    obj.api_base = RECORDER_BASE
    obj.is_active = True
    obj.set_api_key(RECORDER_KEY)
    obj.save()
    return {
        "id": obj.id,
        "created": created,
        "model_name": obj.model_name,
        "provider": obj.provider,
        "api_base_host": "t02-recorder",
        "api_key_last4": obj.api_key_last4,
        "has_encrypted_key": bool(obj.encrypted_api_key),
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
    return {
        "email": FRONTEND_EMAIL,
        "user_id": user.id,
        "created": created,
        "org_slug": org.slug,
        "is_superuser": user.is_superuser,
    }


def main() -> None:
    payload = {"orgs": {}, "frontend": {}, "models": {}}
    frontend_password = secrets.token_urlsafe(18)
    with transaction.atomic():
        User = get_user_model()
        owner = User.objects.filter(is_superuser=True).order_by("id").first()
        if owner is None:
            raise SystemExit("run ensure_zeroshield_admin first")
        for slug, mode, pii in (
            ("v3a02-block", "block", True),
            ("v3a02-observe", "monitor", True),
        ):
            org = _ensure_org(slug, slug)
            _configure_firewall(org, mode=mode, pii=pii)
            key, raw = GatewayAPIKey.ensure_simulator_for_org(org, owner)
            # generate_key copies the owner's org (zeroshield). Force the synth tenant
            # so gateway auth org_slug matches llm:model_configs:{slug}.
            if key.organization_id != org.id:
                key.organization = org
                key.save(update_fields=["organization"])
            payload["orgs"][slug] = {
                "org_id": org.id,
                "key_id": str(key.id),
                "key_prefix": key.prefix,
                "last4": (raw or "")[-4:] if raw else None,
                "raw": raw,
            }
            payload["models"][slug] = _ensure_recorder_model(org)
        block_org = Organization.objects.get(slug="v3a02-block")
        payload["frontend"] = _ensure_frontend_user(block_org, frontend_password)
        payload["frontend"]["password"] = frontend_password
        payload["recorder_api_base"] = RECORDER_BASE
        payload["recorder_key_last4"] = RECORDER_KEY[-4:]

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
        "recorder_api_base": RECORDER_BASE,
        "recorder_key_last4": payload["recorder_key_last4"],
        "keys_path": KEYS_PATH,
        "mode": oct(stat.S_IMODE(os.stat(KEYS_PATH).st_mode)),
    }
    print(json.dumps(public, indent=2))


if __name__ == "__main__":
    main()
