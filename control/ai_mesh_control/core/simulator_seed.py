"""Dev-only bootstrap for simulator default gateway API key in Redis."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SIMULATOR_DEFAULT_KEY_REDIS = "simulator:default_gateway_key"
ZEROSHIELD_GUARD_MODEL_NAME = "zeroshield-guard-120b"


def ensure_default_llm_model(org) -> None:
    """Ensure org has an active ZeroShield guard model for gateway routing (dev bootstrap)."""
    if org is None:
        return
    from core.models import LLMModelConfig

    model_id = os.getenv("ZEROSHIELD_GUARD_MODEL_ID", "bedrock/openai.gpt-oss-120b-1:0").strip()
    if not model_id:
        model_id = "bedrock/openai.gpt-oss-120b-1:0"

    LLMModelConfig.objects.update_or_create(
        organization=org,
        model_name=ZEROSHIELD_GUARD_MODEL_NAME,
        defaults={
            "provider": "internal",
            "model_id": model_id,
            "api_key_env_var": "AWS_ACCESS_KEY_ID",
            "is_active": True,
            "routing_priority": 100,
            "data_sensitivity_level": "restricted",
            "compliance_tags": ["HIPAA", "SOC2"],
        },
    )


def ensure_simulator_firewall_keywords_cleared(org) -> None:
    """
    Dev bootstrap: clear default blocked_keywords so Attack Simulator clean prompts pass.

    Policy Management rules are unchanged; only the firewall keyword list is cleared.
    """
    if org is None:
        return
    clear_kw = os.getenv("SIMULATOR_CLEAR_BLOCKED_KEYWORDS", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not clear_kw:
        return
    from core.models import FirewallConfig

    for config in FirewallConfig.objects.filter(organization=org):
        if (config.blocked_keywords or "").strip():
            config.blocked_keywords = ""
            config.save(update_fields=["blocked_keywords"])


def ensure_firewall_excludes_guard_model(org) -> None:
    """
    Strip the ZeroShield guard model from every firewall allowlist (self-healing).

    The guard model is a platform-internal (``provider=internal``) tier-2 scanning
    model invoked out-of-band; it is never a client inference model and must not
    live in the user-facing ``allowed_models`` governance list. Leaving it there
    makes the governance validator reject saves (it is excluded from the
    user-managed connected set), so this bootstrap removes any stale guard entry.
    """
    from core.models import FirewallConfig, platform_guard_model_names

    guard_names = {name.strip().lower() for name in platform_guard_model_names()}

    configs = list(FirewallConfig.objects.all())
    if org is not None:
        config, _ = FirewallConfig.objects.get_or_create(organization=org)
        if config not in configs:
            configs.append(config)

    for config in configs:
        models = [m.strip() for m in (config.allowed_models or "").split(",") if m.strip()]
        cleaned = [m for m in models if m.strip().lower() not in guard_names]
        if cleaned != models:
            config.allowed_models = ", ".join(cleaned)
            config.save(update_fields=["allowed_models"])


def _resolve_bootstrap_org():
    """Pick the tenant org used for simulator defaults and dev bootstrap."""
    from django.contrib.auth import get_user_model

    from auth.models import Organization

    slug = os.getenv("SIMULATOR_ORG_SLUG", "").strip()
    if slug:
        org = Organization.objects.filter(slug=slug, is_active=True).first()
        if org:
            return org

    org = Organization.objects.filter(slug="zeroshield", is_active=True).first()
    if org:
        return org

    User = get_user_model()
    for email in ("admin@zeroshield.io",):
        user = User.objects.filter(email=email).first()
        if user:
            try:
                if user.profile.organization_id:
                    return user.profile.organization
            except Exception:
                pass

    return Organization.objects.filter(is_active=True).order_by("id").first()


def ensure_simulator_default_gateway_key() -> bool:
    """
    Seed ``simulator:default_gateway_key`` when missing (DEBUG + enabled).

    Returns True if a key exists or was created, False when skipped/disabled.
    """
    from django.conf import settings

    enabled = os.getenv(
        "SIMULATOR_DEFAULTS_ENABLED",
        "true" if getattr(settings, "DEBUG", False) else "false",
    ).lower() in ("1", "true", "yes", "on")
    if not enabled:
        return False

    import redis

    redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")

    try:
        from core.models import GatewayAPIKey

        org = _resolve_bootstrap_org()
        sim_key = GatewayAPIKey.objects.filter(name="simulator-default").first()
        if sim_key and sim_key.organization_id:
            org = sim_key.organization
        elif org and sim_key and sim_key.organization_id != org.id:
            sim_key.organization = org
            sim_key.save(update_fields=["organization"])
        ensure_default_llm_model(org)
        ensure_firewall_excludes_guard_model(org)
        ensure_simulator_firewall_keywords_cleared(org)
    except Exception:
        logger.warning("Simulator LLM model seed failed", exc_info=True)

    try:
        client = redis.Redis.from_url(
            redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=3
        )
        if client.get(SIMULATOR_DEFAULT_KEY_REDIS):
            return True
    except redis.RedisError as exc:
        logger.warning("Simulator key seed skipped (Redis): %s", exc)
        return False

    try:
        from django.contrib.auth import get_user_model

        from core.models import GatewayAPIKey

        User = get_user_model()
        org = _resolve_bootstrap_org()
        existing = GatewayAPIKey.objects.filter(name="simulator-default").first()
        if existing and existing.organization_id:
            org = existing.organization
        user = (
            User.objects.filter(email="admin@zeroshield.io").first()
            or User.objects.first()
        )
        if not user:
            logger.warning("Simulator key seed skipped: no users in database")
            return False

        inst, raw = GatewayAPIKey.generate_key(
            name="simulator-default",
            owner=user,
            project_id="simulator-default",
            allowed_models=[],
        )
        if org and inst.organization_id != org.id:
            inst.organization = org
            inst.save(update_fields=["organization"])
        client.set(SIMULATOR_DEFAULT_KEY_REDIS, raw)
        logger.info("Seeded simulator default gateway key (prefix=%s)", inst.prefix)
        return True
    except Exception:
        logger.warning("Simulator key seed failed", exc_info=True)
        return False
