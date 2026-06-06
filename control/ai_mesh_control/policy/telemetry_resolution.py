"""Resolve policy/rule linkage from gateway telemetry metadata."""

from __future__ import annotations

from policy.constants import ACTION_BLOCK, ACTION_MONITOR, ACTION_REDACT
from policy.models import Policy, Rule

ENFORCEMENT_ACTIONS = (ACTION_BLOCK, ACTION_REDACT, ACTION_MONITOR)


def _meta_bucket(meta: dict) -> dict:
    extra = meta.get("extra") if isinstance(meta.get("extra"), dict) else {}
    return {**extra, **meta}


def metadata_policy_codes(meta: dict | None) -> list[str]:
    """Policy codes from FK-less telemetry or policy-engine events."""
    if not meta:
        return []
    bucket = _meta_bucket(meta)

    def _normalize(val) -> list[str]:
        if isinstance(val, list):
            return [str(c).strip() for c in val if c and str(c).strip()]
        if isinstance(val, str) and val.strip():
            return [val.strip()]
        return []

    # policy_violations is the canonical drain-derived field; prefer it when set.
    preferred = _normalize(bucket.get("policy_violations"))
    if preferred:
        return list(dict.fromkeys(preferred))

    codes: list[str] = []
    for key in ("matched_policy_codes", "matched_policies"):
        codes.extend(_normalize(bucket.get(key)))
    return list(dict.fromkeys(codes))


def metadata_rule_names(meta: dict | None) -> list[str]:
    """Rule display names from telemetry metadata."""
    if not meta:
        return []
    bucket = _meta_bucket(meta)
    names: list[str] = []
    for key in ("matched_rule_names", "matched_rules"):
        val = bucket.get(key)
        if isinstance(val, list):
            names.extend(str(n).strip() for n in val if n)
        elif isinstance(val, str) and val.strip():
            names.append(val.strip())
    return list(dict.fromkeys(names))


def metadata_matched_ids(meta: dict | None, key: str) -> list[int]:
    if not meta:
        return []
    bucket = _meta_bucket(meta)
    raw = bucket.get(key)
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def resolve_policy_rule_from_event(
    *,
    action: str,
    organization_id: int | None,
    raw_metadata: dict | None,
    built_metadata: dict | None = None,
) -> tuple[Policy | None, Rule | None]:
    """
    Resolve Policy/Rule FKs for a drained telemetry event.
    Mirrors evaluation_views.PolicyCheckView (first match wins).
    """
    if action not in ENFORCEMENT_ACTIONS:
        return None, None

    meta = {**((built_metadata or {})), **((raw_metadata or {}))}
    policy_ids = metadata_matched_ids(meta, "matched_policy_ids")
    rule_ids = metadata_matched_ids(meta, "matched_rule_ids")

    policy: Policy | None = None
    rule: Rule | None = None

    if policy_ids:
        qs = Policy.objects.filter(pk=policy_ids[0])
        if organization_id:
            qs = qs.filter(organization_id=organization_id)
        policy = qs.first()

    if rule_ids:
        qs = Rule.objects.filter(pk=rule_ids[0]).select_related("policy")
        if organization_id:
            qs = qs.filter(policy__organization_id=organization_id)
        rule = qs.first()
        if rule and policy and rule.policy_id != policy.id:
            rule = None

    if policy is None:
        codes = metadata_policy_codes(meta)
        if codes:
            qs = Policy.objects.filter(code=codes[0])
            if organization_id:
                qs = qs.filter(organization_id=organization_id)
            policy = qs.first()

    if rule is None and policy is not None:
        names = metadata_rule_names(meta)
        if names:
            rule = Rule.objects.filter(policy=policy, name=names[0]).first()

    return policy, rule
