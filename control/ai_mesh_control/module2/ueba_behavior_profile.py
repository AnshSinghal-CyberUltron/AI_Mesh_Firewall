"""Collect and store first-N prompt samples for UEBA behavior profiling."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from django.conf import settings
from django.utils import timezone

from module2.analytics import key_prefix_from_meta, prompt_snippet_from_meta
from module2.models import ApiKeyBehaviorProfile, OrgUebaSettings

LOG = logging.getLogger(__name__)

DEFAULT_PROMPT_TARGET = 50
SAMPLE_SNIPPET_MAX_LEN = 200


def prompt_target() -> int:
    return int(getattr(settings, "MODULE2_UEBA_BEHAVIOR_PROMPT_TARGET", DEFAULT_PROMPT_TARGET))


def prompt_target_for_org(org_id: int | None, org_settings=None) -> int:
    if org_settings is not None:
        return int(getattr(org_settings, "behavior_profile_prompt_target", prompt_target()))
    if not org_id:
        return prompt_target()
    settings_obj = OrgUebaSettings.objects.filter(organization_id=org_id).only(
        "behavior_profile_prompt_target"
    ).first()
    if settings_obj is None:
        return prompt_target()
    return int(getattr(settings_obj, "behavior_profile_prompt_target", prompt_target()))


def get_or_create_profile(key) -> ApiKeyBehaviorProfile:
    profile, _ = ApiKeyBehaviorProfile.objects.get_or_create(gateway_api_key=key)
    return profile


def load_profile_for_key(key) -> ApiKeyBehaviorProfile | None:
    profile = getattr(key, "ueba_behavior_profile", None)
    if profile is not None:
        return profile
    try:
        return key.ueba_behavior_profile
    except ApiKeyBehaviorProfile.DoesNotExist:
        return ApiKeyBehaviorProfile.objects.filter(gateway_api_key=key).first()


def _sample_identity(entry: dict) -> str:
    request_id = str(entry.get("request_id") or "").strip()
    snippet = str(entry.get("snippet") or "").strip()
    return f"{request_id}:{snippet}"


def _event_to_sample(ev, *, max_len: int = SAMPLE_SNIPPET_MAX_LEN) -> dict[str, Any] | None:
    meta = getattr(ev, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    snippet = prompt_snippet_from_meta(meta, max_len=max_len)
    if not snippet:
        return None
    created_at = getattr(ev, "created_at", None)
    return {
        "timestamp": created_at.isoformat() if created_at else None,
        "action": getattr(ev, "action", None),
        "model": meta.get("model"),
        "threat_type": meta.get("threat_type"),
        "request_id": meta.get("request_id") or meta.get("pipeline_request_id"),
        "snippet": snippet,
    }


def append_prompt_samples_for_events(events) -> dict[tuple[int, str], int]:
    """
    Append redacted prompt snippets to behavior profiles (max prompt_target per key).
    Returns {(org_id, prefix_lower): samples_added}.
    """
    from core.models import GatewayAPIKey

    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for ev in events:
        meta = getattr(ev, "metadata", None) or {}
        prefix = key_prefix_from_meta(meta)
        org_id = getattr(ev, "organization_id", None)
        if not prefix or not org_id:
            continue
        sample = _event_to_sample(ev)
        if sample:
            grouped[(int(org_id), prefix.lower())].append(sample)

    if not grouped:
        return {}

    org_ids = {oid for oid, _ in grouped}
    keys_by_org_prefix: dict[tuple[int, str], Any] = {}
    for key in GatewayAPIKey.objects.filter(organization_id__in=org_ids).only(
        "id", "organization_id", "prefix"
    ):
        keys_by_org_prefix[(key.organization_id, key.prefix.lower())] = key

    added: dict[tuple[int, str], int] = {}
    for (org_id, prefix_lower), samples in grouped.items():
        key = keys_by_org_prefix.get((org_id, prefix_lower))
        if not key:
            continue
        target = prompt_target_for_org(org_id)
        profile = get_or_create_profile(key)
        if profile.profile_built_at is not None:
            continue
        if profile.sample_count >= target:
            continue
        existing = list(profile.prompt_samples or [])
        seen = {_sample_identity(s) for s in existing if isinstance(s, dict)}
        new_count = 0
        for sample in samples:
            if profile.sample_count + new_count >= target:
                break
            ident = _sample_identity(sample)
            if ident in seen:
                continue
            seen.add(ident)
            existing.append(sample)
            new_count += 1
        if new_count:
            profile.prompt_samples = existing[:target]
            profile.sample_count = len(profile.prompt_samples)
            profile.save(update_fields=["prompt_samples", "sample_count", "updated_at"])
            added[(org_id, prefix_lower)] = new_count
    return added


def needs_bootstrap(profile: ApiKeyBehaviorProfile | None, org_settings=None) -> bool:
    if profile is None:
        return False
    target = prompt_target_for_org(
        getattr(getattr(profile, "gateway_api_key", None), "organization_id", None),
        org_settings=org_settings,
    )
    return profile.sample_count >= target and profile.profile_built_at is None


def profile_is_ready(profile: ApiKeyBehaviorProfile | None) -> bool:
    """Profile stays ready once built, even if org prompt target changes later."""
    return profile is not None and profile.profile_built_at is not None


def profile_build_sample_count(profile: ApiKeyBehaviorProfile | None) -> int:
    if profile is None:
        return 0
    baseline = dict(profile.baseline_metrics or {})
    built_count = int(baseline.get("sample_count") or 0)
    if built_count > 0:
        return built_count
    return int(profile.sample_count or 0)


def fetch_first_n_snippets(
    profile: ApiKeyBehaviorProfile | None,
    n: int | None = None,
    org_settings=None,
) -> list[dict]:
    if profile is None:
        return []
    if n is None:
        n = prompt_target_for_org(
            getattr(getattr(profile, "gateway_api_key", None), "organization_id", None),
            org_settings=org_settings,
        )
    samples = profile.prompt_samples or []
    return [s for s in samples[:n] if isinstance(s, dict)]


def behavior_profile_payload(profile: ApiKeyBehaviorProfile | None, org_settings=None) -> dict[str, Any]:
    target = prompt_target_for_org(
        getattr(getattr(profile, "gateway_api_key", None), "organization_id", None),
        org_settings=org_settings,
    )
    if profile is None:
        return {
            "status": "building",
            "prompt_samples_collected": 0,
            "prompt_samples_target": target,
            "expected_use_case": "",
            "behavior_class": "unknown",
            "risk_prediction": "",
            "profile_built_at": None,
        }
    status = "ready" if profile.profile_built_at else "building"
    build_count = profile_build_sample_count(profile)
    return {
        "status": status,
        "profile_locked": bool(profile.profile_built_at),
        "prompt_samples_collected": int(profile.sample_count or 0),
        "prompt_samples_target": target,
        "prompt_samples_build_count": build_count if profile.profile_built_at else None,
        "expected_use_case": profile.expected_use_case or "",
        "behavior_class": profile.behavior_class or "unknown",
        "risk_prediction": profile.risk_prediction or "",
        "baseline_metrics": dict(profile.baseline_metrics or {}),
        "profile_built_at": (
            profile.profile_built_at.isoformat() if profile.profile_built_at else None
        ),
    }


def save_bootstrap_result(profile: ApiKeyBehaviorProfile, result: dict[str, Any]) -> ApiKeyBehaviorProfile:
    profile.expected_use_case = str(result.get("expected_use_case", ""))[:4000]
    behavior_class = str(result.get("behavior_class", "unknown")).lower()
    valid_classes = {c[0] for c in ApiKeyBehaviorProfile.BEHAVIOR_CLASS_CHOICES}
    profile.behavior_class = behavior_class if behavior_class in valid_classes else "unknown"
    profile.risk_prediction = str(result.get("risk_prediction", ""))[:4000]
    conf = result.get("confidence")
    profile.llm_confidence = float(conf) if conf is not None else None
    sample_rows = [s for s in (profile.prompt_samples or []) if isinstance(s, dict)]
    sample_total = max(len(sample_rows), 1)
    blocked = sum(1 for s in sample_rows if str(s.get("action") or "") == "block")
    redacted = sum(1 for s in sample_rows if str(s.get("action") or "") == "redact")
    model_set = sorted({str(s.get("model") or "").strip() for s in sample_rows if s.get("model")})
    threat_counts: dict[str, int] = defaultdict(int)
    for s in sample_rows:
        threat = str(s.get("threat_type") or "unknown").strip() or "unknown"
        threat_counts[threat] += 1

    profile.baseline_metrics = {
        "sample_count": len(sample_rows),
        "block_rate": round(blocked / sample_total, 4),
        "redact_rate": round(redacted / sample_total, 4),
        "models": model_set,
        "top_threats": sorted(threat_counts.items(), key=lambda x: -x[1])[:5],
    }
    profile.profile_built_at = timezone.now()
    profile.save(
        update_fields=[
            "expected_use_case",
            "behavior_class",
            "risk_prediction",
            "llm_confidence",
            "baseline_metrics",
            "profile_built_at",
            "updated_at",
        ]
    )
    return profile


def prefixes_from_events(events) -> dict[int, set[str]]:
    """Map org_id -> set of canonical key prefixes seen in events."""
    from core.models import GatewayAPIKey

    raw: dict[int, set[str]] = defaultdict(set)
    for ev in events:
        meta = getattr(ev, "metadata", None) or {}
        prefix = key_prefix_from_meta(meta)
        org_id = getattr(ev, "organization_id", None)
        if prefix and org_id:
            raw[int(org_id)].add(prefix.lower())

    result: dict[int, set[str]] = defaultdict(set)
    for org_id, lowers in raw.items():
        lookup = {
            k.prefix.lower(): k.prefix
            for k in GatewayAPIKey.objects.filter(organization_id=org_id).only("prefix")
        }
        for lower in lowers:
            canonical = lookup.get(lower)
            if canonical:
                result[org_id].add(canonical)
    return result
