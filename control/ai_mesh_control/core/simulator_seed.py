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
            "provider": "custom",
            "model_id": model_id,
            "api_key_env_var": "AWS_ACCESS_KEY_ID",
            "is_active": True,
            "routing_priority": 100,
            "data_sensitivity_level": "restricted",
            "compliance_tags": ["HIPAA", "SOC2"],
        },
    )


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
        from django.contrib.auth import get_user_model

        from auth.models import Organization

        org = Organization.objects.first()
        ensure_default_llm_model(org)
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

        from auth.models import Organization
        from core.models import GatewayAPIKey

        User = get_user_model()
        org = Organization.objects.first()
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
        if org and not inst.organization_id:
            inst.organization = org
            inst.save(update_fields=["organization"])
        client.set(SIMULATOR_DEFAULT_KEY_REDIS, raw)
        logger.info("Seeded simulator default gateway key (prefix=%s)", inst.prefix)
        return True
    except Exception:
        logger.warning("Simulator key seed failed", exc_info=True)
        return False
