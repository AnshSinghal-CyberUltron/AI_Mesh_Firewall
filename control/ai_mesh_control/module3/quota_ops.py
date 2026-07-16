"""Rolling token-quota usage helpers for Module 3 API governance."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from module3.models import ApiQuotaPolicy, ApiQuotaUsage


def ensure_usage(policy: ApiQuotaPolicy) -> ApiQuotaUsage:
    usage, _ = ApiQuotaUsage.objects.get_or_create(
        organization=policy.organization,
        policy=policy,
        defaults={
            "tokens_minute": 0,
            "tokens_day": 0,
            "minute_window_start": timezone.now(),
            "day_window_start": timezone.now(),
        },
    )
    return usage


def _roll_windows(usage: ApiQuotaUsage, now=None) -> ApiQuotaUsage:
    now = now or timezone.now()
    changed = False
    if usage.minute_window_start is None or (now - usage.minute_window_start) >= timedelta(minutes=1):
        usage.tokens_minute = 0
        usage.minute_window_start = now
        changed = True
    if usage.day_window_start is None or (now - usage.day_window_start) >= timedelta(days=1):
        usage.tokens_day = 0
        usage.day_window_start = now
        changed = True
    if changed:
        usage.save(update_fields=["tokens_minute", "tokens_day", "minute_window_start", "day_window_start", "updated_at"])
    return usage


def increment_usage(policy: ApiQuotaPolicy, tokens: int) -> ApiQuotaUsage:
    usage = _roll_windows(ensure_usage(policy))
    usage.tokens_minute = int(usage.tokens_minute or 0) + int(tokens)
    usage.tokens_day = int(usage.tokens_day or 0) + int(tokens)
    usage.save(update_fields=["tokens_minute", "tokens_day", "updated_at"])
    return usage


def build_opa_quota_snapshot(org) -> dict:
    """Nested document: quotas[tenant][environment] = {...} for OPA data.module3.quotas."""
    out: dict = {}
    policies = ApiQuotaPolicy.objects.filter(organization=org).prefetch_related("usage")
    for policy in policies:
        usage = _roll_windows(ensure_usage(policy))
        tenant = out.setdefault(policy.tenant_id, {})
        tenant[policy.environment] = {
            "tpm": int(policy.tokens_per_minute),
            "tpd": int(policy.tokens_per_day),
            "used_minute": int(usage.tokens_minute or 0),
            "used_day": int(usage.tokens_day or 0),
            "enabled": bool(policy.enabled),
            "denied_paths": list(policy.denied_paths or []),
        }
    return out
