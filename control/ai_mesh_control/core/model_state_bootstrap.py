"""Bootstrap ModelState rows from active LLMModelConfig entries."""

from __future__ import annotations

import logging

from core.models import (
    LLMModelConfig,
    ModelState,
    is_platform_managed_llm_model_name,
)

logger = logging.getLogger(__name__)


def canonicalize_model_name_safe(name):
    """Collapse reserved/guard model ids (including variants like
    ``anthropic/claude-haiku-4.5`` or ``bedrock/global.anthropic.claude-haiku-...``)
    to the public ``zeroshield-model`` label, using the SAME token-based
    canonicalizer the threat-feed applies. Falls back to the raw name if the
    canonicalizer cannot be imported (lazy import avoids a core<->policy cycle).
    """
    try:
        from policy.security_views import _canonicalize_model_name
        return _canonicalize_model_name(name)
    except Exception:  # pragma: no cover - defensive
        return name


def is_reserved_guard_model_name(name) -> bool:
    """True if ``name`` is a platform/guard identifier under the token-based
    canonicalizer. This catches raw upstream/variant guard ids that the
    exact-match ``platform_guard_model_names()`` set misses (the model-id-leak
    class previously fixed only on the threat-feed surface)."""
    if not name:
        return False
    return canonicalize_model_name_safe(name) == canonicalize_model_name_safe("zeroshield-guard-120b")


def ensure_model_states_for_org(organization) -> tuple[int, int]:
    """
    Idempotently create ModelState for each active LLMModelConfig on the org.

    Returns (created_count, total_active_configs).
    """
    if organization is None:
        return 0, 0

    configs = LLMModelConfig.queryset_user_managed(
        LLMModelConfig.objects.filter(
            organization=organization,
            is_active=True,
        )
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

    configs = LLMModelConfig.queryset_user_managed(
        LLMModelConfig.objects.filter(
            organization=organization,
            is_active=True,
        )
    ).only("model_name", "model_id")
    config_by_name = {c.model_name: c.model_id for c in configs}

    merged = []
    seen = set()
    _orphan_ids = []  # P9: active orphans with no config — reap, don't just hide

    for state in states_qs:
        # Drop platform/guard models — exact-match names AND token-based variants
        # (raw upstream ids like anthropic/claude-haiku-4.5) so the Risk Monitor
        # never surfaces a guard identifier. Dropping (not relabeling) keeps the
        # isolate/recover action round-trip — which is keyed on model_name — intact.
        if is_platform_managed_llm_model_name(state.model_name) or is_reserved_guard_model_name(state.model_name):
            continue
        # Read-time prune: a ModelState whose model_name is no longer a connected
        # (active, user-managed) config is an orphan left over from prior testing
        # or a since-removed model. Hide pure noise (status 'active'); KEEP active
        # containment (isolated/degraded) so an operator never loses sight of a
        # model that is currently under enforcement even if its config was removed.
        if (
            state.model_name not in config_by_name
            and state.status not in ("isolated", "degraded")
        ):
            # P9: orphan with no live config and not under active containment.
            # Previously only HIDDEN here, so the DB row persisted forever and the
            # read-view drifted from the DB. Collect it for a bulk reap below.
            if state.id is not None:
                _orphan_ids.append(state.id)
            continue
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

    # P9: DB-reap the hidden orphans so the read-view and DB converge (fires the
    # existing post_delete Redis cleanup). Best-effort — a reap failure must never
    # break the read path. Platform/guard + isolated/degraded rows were excluded
    # above and are never collected here.
    if _orphan_ids:
        try:
            ModelState.objects.filter(id__in=_orphan_ids).delete()
        except Exception:
            logger.warning(
                "P9: orphan ModelState reap failed (org=%s ids=%s)",
                getattr(organization, "id", None),
                _orphan_ids,
                exc_info=True,
            )

    merged.sort(key=lambda row: row["model_name"])
    return merged
