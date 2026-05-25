"""Backfill historical MCPEvent rows into EnforcementEvent so the AI Mesh
Firewall dashboard graphs and Module 1.4 'Recent MCP and context evidence'
table reflect MCP traffic recorded before the live mirror was deployed.

Idempotent: skips MCPEvent rows that already have an EnforcementEvent with
metadata.request_id == mcp_event.request_id (set by the mirror).

Usage:
    docker exec ai_security-backend-1 python manage.py backfill_mcp_events --org=zeroshield
    # Or all orgs:
    docker exec ai_security-backend-1 python manage.py backfill_mcp_events
    # Dry-run:
    docker exec ai_security-backend-1 python manage.py backfill_mcp_events --org=zeroshield --dry-run
"""
from django.core.management.base import BaseCommand

from mcp_connector.models import MCPEvent
from policy.models import EnforcementEvent


_ACTION_MAP = {
    "block": "block",
    "redact": "redact",
    "monitor": "monitor",
    "allow": "monitor",
    "error": "monitor",
}
_RISK_MAP = {"block": 85, "redact": 55, "monitor": 25, "allow": 10, "error": 40}
_STATUS_MAP = {"block": 403, "redact": 200, "monitor": 200, "allow": 200, "error": 502}


def _classify(decision: str, reason: str, policy_ids):
    r = (reason or "").lower()
    if decision == "block":
        if "schema" in r:
            return "MCP Schema Violation", "MCP08"
        if "tool_disabled" in r:
            return "MCP Tool Disabled", "MCP01"
        if "policy" in r or policy_ids:
            return "MCP Policy Violation", "MCP02"
        return "MCP Tool Block", "MCP01"
    if decision == "redact":
        return "MCP Data Redaction", "MCP06"
    if decision == "error":
        return "MCP Tool Error", "MCP01"
    return "MCP Tool Call", "MCP01"


class Command(BaseCommand):
    help = "Backfill EnforcementEvent rows from historical MCPEvent records."

    def add_arguments(self, parser):
        parser.add_argument("--org", default=None, help="Organization slug (default: all orgs)")
        parser.add_argument("--dry-run", action="store_true", help="Print plan without writing")
        parser.add_argument("--limit", type=int, default=0, help="Limit number of events processed (0 = all)")

    def handle(self, *args, **opts):
        qs = MCPEvent.objects.all().order_by("timestamp")
        if opts["org"]:
            from auth.models import Organization
            try:
                org = Organization.objects.get(slug=opts["org"])
            except Organization.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Org slug not found: {opts['org']}"))
                return
            qs = qs.filter(organization=org)
        if opts["limit"]:
            qs = qs[: opts["limit"]]

        # Build set of already-mirrored request_ids in EnforcementEvent so we
        # don't double-write.
        existing_req_ids = set(
            EnforcementEvent.objects.filter(metadata__source="mcp_scan")
            .values_list("metadata__request_id", flat=True)
        )

        created = 0
        skipped = 0
        no_org = 0
        total = qs.count()
        self.stdout.write(f"Scanning {total} MCPEvent rows...")

        for ev in qs.iterator():
            if ev.organization_id is None:
                no_org += 1
                continue
            req_id = ev.request_id or ""
            if req_id and req_id in existing_req_ids:
                skipped += 1
                continue

            decision = ev.decision or "monitor"
            action = _ACTION_MAP.get(decision, "monitor")
            risk = _RISK_MAP.get(decision, 10)
            status_code = _STATUS_MAP.get(decision, 200)
            policy_ids = list(ev.policy_ids or [])
            cat, owasp = _classify(decision, ev.policy_reason or "", policy_ids)

            existing_meta = dict(ev.metadata or {})
            metadata = {
                "source": "mcp_scan",
                "threat_category": cat,
                "owasp_code": owasp,
                "decision": decision,
                "reason": ev.policy_reason or "",
                "tool_name": ev.tool_name or "",
                "tools_invoked": [ev.tool_name] if ev.tool_name else [],
                "data_accessed": [ev.server_slug] if ev.server_slug else [],
                "server_slug": ev.server_slug or "",
                "server_name": ev.server_name or "",
                "request_id": req_id,
                "pipeline_request_id": req_id,
                "latency_ms": ev.latency_ms or 0,
                "policy_ids": policy_ids,
                "security_risk_score": risk,
                "actor_username": ev.username or "",
                "method": "POST",
                "endpoint": f"/api/mcp-connector/tools/call/ ({ev.tool_name})",
                "source_ip": existing_meta.get("source_ip", ""),
                "user_agent": existing_meta.get("user_agent", ""),
                "model": ev.tool_name or "",
                "status_code": status_code,
                "pipeline_stage": "mcp_tool_call",
                "intent": f"mcp:{ev.tool_name or 'unknown'}",
                "event_type": "mcp_tool_call",
                "compliance_tags": ["OWASP-MCP", owasp],
                "rate_limit_status": "n/a",
                "auth_status": "authenticated" if ev.user_id else "anonymous",
                "input_validation": "schema_failed" if "schema" in (ev.policy_reason or "").lower() else "schema_ok",
                "content_safety": "violation" if decision == "block" else "ok",
                "pii_detected": decision == "redact",
                "prompt_injection_detected": False,
                "jailbreak_detected": False,
                "policy_violations": [],
                "extra": {
                    "matched_patterns": [],
                    "request_id": req_id,
                    "backfilled": True,
                },
                "backfilled": True,
            }

            if opts["dry_run"]:
                created += 1
                continue

            EnforcementEvent.objects.create(
                organization=ev.organization,
                policy=None,
                rule=None,
                action=action,
                user_id=ev.user_id,
                metadata=metadata,
                created_at=ev.timestamp,
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Backfill complete: created={created} skipped(existing)={skipped} skipped(no_org)={no_org} total_scanned={total} dry_run={opts['dry_run']}"
        ))
