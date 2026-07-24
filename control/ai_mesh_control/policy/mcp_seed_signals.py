"""
Auto-seed the baseline MCP guardrail policies when an Organization is created:
the PII policy (``seed_mcp_pii_policy``) AND the Phase-2b detector policies that
replicate each active server's posture + scan-control enforcement
(``seed_mcp_detector_policies``). Together with the 0038 backfill data migration
(which seeds every EXISTING org), this guarantees NO org is ever un-seeded — so
the Phase-3 ``mcp_policy_only_enforcement`` cutover flag is always safe to flip
(the seeded detector policies carry the coverage the retired posture provided).
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from auth.models import Organization

logger = logging.getLogger(__name__)


def _reseed_org_detectors(org, *, why):
    """Best-effort idempotent re-seed of an org's MCP detector policies. Never raises — a seeding
    failure is logged and never blocks the operator save/delete that triggered it."""
    if org is None:
        return
    try:
        from policy.mcp_seed import seed_mcp_detector_policies

        seed_mcp_detector_policies(org)
    except Exception:  # pragma: no cover
        logger.warning("Failed to re-seed MCP detector policies (%s) for org=%s",
                       why, getattr(org, "id", "?"), exc_info=True)


@receiver(post_save, sender=Organization, dispatch_uid="seed_mcp_policies_on_org_create")
def _seed_mcp_policies_on_org_create(sender, instance, created, **kwargs):
    if not created:
        return
    # Never block org creation on seeding — each guardrail is best-effort and logged.
    try:
        from policy.mcp_seed import seed_mcp_pii_policy

        seed_mcp_pii_policy(instance)
    except Exception:  # pragma: no cover
        logger.warning(
            "Failed to auto-seed MCP PII policy for org=%s", instance.id, exc_info=True
        )
    try:
        from policy.mcp_seed import seed_mcp_detector_policies

        seed_mcp_detector_policies(instance)
    except Exception:  # pragma: no cover
        logger.warning(
            "Failed to auto-seed MCP detector policies for org=%s", instance.id, exc_info=True
        )


@receiver(post_save, sender="mcp_connector.MCPServerRegistration",
          dispatch_uid="seed_mcp_detector_policies_on_server_save")
def _seed_mcp_detector_policies_on_server_save(sender, instance, **kwargs):
    """(Re-)seed the org's MCP detector policies whenever a server is REGISTERED or its posture
    changes. Detector policies are PER-SERVER, so the org-create signal (no servers yet at create)
    can't seed them — this is the effective trigger that keeps a NEW server from being un-seeded at
    Phase-3 cutover. Seeding is idempotent (reconciles to the current posture + scan-controls), so
    re-running on every save is safe; a failure is logged and never blocks the server save."""
    _reseed_org_detectors(getattr(instance, "organization", None), why="server_save")


@receiver(pre_delete, sender="mcp_connector.MCPServerRegistration",
          dispatch_uid="disable_user_policies_on_server_delete")
def _disable_user_policies_on_server_delete(sender, instance, **kwargs):
    """Server/tool-binding red-team (wf_9ca3814d): ``Policy.mcp_server`` is SET_NULL, so deleting a
    server NULLs the FK of every policy bound to it — and the compiler stamps a NULL ``mcp_server`` as
    ORG-WIDE, so a user's server-SCOPED rule SILENTLY begins enforcing on every OTHER server (invariant
    E cross-server bleed: a block/redact over-enforces org-wide; an allow/exemption becomes an org-wide
    bypass). The seeded detector policy is hard-deleted by the post_delete handler below, but a
    USER-authored (``is_system=False``) policy has a different ``code`` and survives with
    ``mcp_server=NULL``. Capture those HERE in pre_delete (the FK is still set) and DISABLE them, so the
    deleted server's scope can never silently widen to org-wide. Disable (not delete) preserves the
    operator's authored rule; re-enabling an unbound policy is then an explicit, visible operator
    action, not a silent conversion. Runs before SET_NULL + the seeded-delete recompile, so the next
    compile already excludes the disabled rule."""
    try:
        from policy.models import Policy

        Policy.objects.filter(
            mcp_server=instance, is_system=False, enabled=True
        ).update(enabled=False)
    except Exception:  # pragma: no cover
        logger.warning("Failed to disable server-bound user policies on server delete for server=%s",
                       getattr(instance, "id", "?"), exc_info=True)


@receiver(post_delete, sender="mcp_connector.MCPServerRegistration",
          dispatch_uid="delete_mcp_detector_policy_on_server_delete")
def _delete_mcp_detector_policy_on_server_delete(sender, instance, **kwargs):
    """Integration red-team wf_21ddb986 #3: deleting a server ORPHANS its seeded detector policy —
    ``Policy.mcp_server`` is SET_NULL, so the system policy stays ENABLED and keeps enforcing its
    block/redact rules ORG-WIDE (mcp_server=NULL matches every server). Hard-delete the orphan on
    server delete, capturing the org + server id from the instance (still populated in post_delete)."""
    org = getattr(instance, "organization", None)
    org_id = getattr(org, "id", None)
    if org is None or org_id is None:
        return
    try:
        from policy.mcp_seed import detector_policy_code
        from policy.models import Policy

        Policy.objects.filter(
            code=detector_policy_code(org_id, instance.id), organization=org
        ).delete()
    except Exception:  # pragma: no cover
        logger.warning("Failed to delete orphan MCP detector policy on server delete for org=%s",
                       org_id, exc_info=True)


# The detector policies replicate the server posture AND the Tier-1 SCAN-CONTROL / per-tool ACTIONS,
# so those surfaces must ALSO re-seed on change — else an operator scan-control/tool edit after the
# backfill drifts the seeded policies STALE, and flipping the Phase-3 flag retires the posture +
# scan-control action in favour of policies that no longer reflect the operator's config → raw
# egress (red-team wf_11139764). post_DELETE matters too: the seeder reconcile retires the stale
# rule when an enforcing control/tool override is removed.
@receiver(post_save, sender="mcp_connector.MCPScanControl",
          dispatch_uid="seed_mcp_detectors_on_scan_control_save")
@receiver(post_delete, sender="mcp_connector.MCPScanControl",
          dispatch_uid="seed_mcp_detectors_on_scan_control_delete")
def _reseed_on_scan_control_change(sender, instance, **kwargs):
    _reseed_org_detectors(getattr(instance, "organization", None), why="scan_control_change")


@receiver(post_save, sender="mcp_connector.MCPToolRegistration",
          dispatch_uid="seed_mcp_detectors_on_tool_save")
@receiver(post_delete, sender="mcp_connector.MCPToolRegistration",
          dispatch_uid="seed_mcp_detectors_on_tool_delete")
def _reseed_on_tool_change(sender, instance, **kwargs):
    server = getattr(instance, "server", None)
    _reseed_org_detectors(getattr(server, "organization", None), why="tool_change")
