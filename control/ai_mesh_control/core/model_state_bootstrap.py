"""Bootstrap ModelState rows from active LLMModelConfig entries."""

from __future__ import annotations

import logging

from core.models import LLMModelConfig, ModelState

logger = logging.getLogger(__name__)


def ensure_model_states_for_org(organization) -> tuple[int, int]:
    """
    Idempotently create ModelState for each active LLMModelConfig on the org.

    Returns (created_count, total_active_configs).
    """
    if organization is None:
        return 0, 0

    configs = LLMModelConfig.objects.filter(
        organization=organization,
        is_active=True,
    ).only("model_name")

    created = 0
    for cfg in configs:
        _, was_created = ModelState.objects.get_or_create(
            organization=organization,
            model_name=cfg.model_name,
            defaults={
                "status": "active",
                "risk_score": 0.0,
                "threshold": 80.0,
                "action": "block",
            },
        )
        if was_created:
            created += 1
            logger.info(
                "ModelState bootstrapped model=%s org=%s",
                cfg.model_name,
                organization.slug,
            )

    return created, configs.count()


def merge_model_states_with_configs(organization, states_qs):
    """
    Return serialized-ready dicts: DB ModelState rows plus virtual rows for
    active configs that lack a ModelState row yet.
    """
    from core.serializers import ModelStateSerializer

    configs = LLMModelConfig.objects.filter(
        organization=organization,
        is_active=True,
    ).only("model_name", "model_id")
    config_by_name = {c.model_name: c.model_id for c in configs}

    merged = []
    seen = set()

    for state in states_qs:
        data = ModelStateSerializer(state).data
        data["organization_slug"] = organization.slug
        data["gateway_model_hint"] = config_by_name.get(state.model_name, state.model_name)
        data["is_bootstrapped"] = True
        merged.append(data)
        seen.add(state.model_name)

    for model_name, model_id in config_by_name.items():
        if model_name in seen:
            continue
        merged.append(
            {
                "id": None,
                "model_name": model_name,
                "status": "active",
                "risk_score": 0.0,
                "threshold": 80.0,
                "action": "block",
                "fallback_model": "",
                "isolation_reason": "",
                "isolated_at": None,
                "isolated_until": None,
                "cooldown_seconds": 300,
                "last_updated": None,
                "created_at": None,
                "organization_slug": organization.slug,
                "gateway_model_hint": model_id,
                "is_bootstrapped": False,
            }
        )

    merged.sort(key=lambda row: row["model_name"])
    return merged
