"""Dev bootstrap helpers for Module 1 simulators (LLM model + firewall defaults)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

ZEROSHIELD_GUARD_MODEL_NAME = "zeroshield-guard-120b"
SIMULATOR_ORG_SLUG = os.getenv("SIMULATOR_ORG_SLUG", "zeroshield").strip().lower()


def _resolve_simulator_org():
    """Prefer the zeroshield tenant (or SIMULATOR_ORG_SLUG) for dev bootstrap."""
    from auth.models import Organization

    slug = SIMULATOR_ORG_SLUG or "zeroshield"
    org = Organization.objects.filter(slug=slug).first()
    if org is not None:
        return org
    return Organization.objects.first()


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
    live in the user-facing ``allowed_models`` governance list.
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


def ensure_simulator_dev_bootstrap(org=None) -> bool:
    """
    Apply dev-only simulator bootstrap (LLM model + firewall helpers).

    Gateway API keys are provisioned lazily via ``SimulatorDefaultGatewayKeyView``
    POST — plaintext is never stored in Redis.
    """
    from django.conf import settings

    enabled = os.getenv(
        "SIMULATOR_DEFAULTS_ENABLED",
        "true" if getattr(settings, "DEBUG", False) else "false",
    ).lower() in ("1", "true", "yes", "on")
    if not enabled:
        return False

    if org is None:
        org = _resolve_simulator_org()
    if org is None:
        return False

    try:
        ensure_default_llm_model(org)
        ensure_firewall_excludes_guard_model(org)
        ensure_simulator_firewall_keywords_cleared(org)
        return True
    except Exception:
        logger.warning("Simulator dev bootstrap failed for org=%s", getattr(org, "slug", org), exc_info=True)
        return False


def ensure_simulator_default_gateway_key() -> bool:
    """Backward-compatible alias for startup scripts — dev bootstrap only."""
    return ensure_simulator_dev_bootstrap()
