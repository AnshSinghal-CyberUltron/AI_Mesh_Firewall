"""Shared KillSwitch audit helpers for views and management commands."""

from __future__ import annotations

from core.models import KillSwitch, KillSwitchAuditLog


def write_kill_switch_audit(
    *,
    instance: KillSwitch,
    event: str,
    triggered_by: str,
    trigger_source: str = "manual",
) -> KillSwitchAuditLog | None:
    """Persist structured audit row when organization is set."""
    org = instance.organization
    if org is None:
        return None
    scope = "org_global" if instance.model_name == KillSwitch.SCOPE_GLOBAL else "org_model"
    credential_prefix = (instance.api_key_prefix or "").strip()
    if credential_prefix:
        scope = "credential"
    return KillSwitchAuditLog.objects.create(
        organization=org,
        event=event,
        model_name=instance.model_name,
        action=instance.action if event != "kill_switch_deactivated" else "deactivate",
        reason=instance.reason,
        triggered_by=triggered_by,
        metadata={
            "scope": scope,
            "api_key_prefix": credential_prefix,
            "fallback_model": instance.fallback_model,
            "trigger_source": trigger_source,
            "org_slug": org.slug,
        },
    )
