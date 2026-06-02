"""Provision an organization for two-tier MCP scanning.

Enables MCP Tier-2 (Bedrock) for an org by:
  1. setting ``FirewallConfig.mcp_tier2_enabled = True`` (the per-org gate), and
  2. creating an org-scoped ``MCPScanControl`` row (tier2, enabled=True) — without
     this row the resolver falls back to the built-in ``DEFAULT_TIER2`` which is
     ``enabled=False``, so Tier-2 never fires (this is exactly why Tier-2 never
     ran for any org before).

``strict_mode`` defaults to ``fail_open`` so that a cold/missing Bedrock
credential degrades to Tier-1 instead of turning every call into a spurious
451 block. Pass ``--strict-mode strict`` for fail-closed enforcement.

Optionally sets a server's ``default_scan_action`` so Tier-1/Tier-2 findings
actually block/redact (the default model value is ``tag`` = observe only).
"""
from django.core.management.base import BaseCommand

from auth.models import Organization
from core.models import FirewallConfig
from mcp_connector.models import MCPScanControl, MCPServerRegistration


class Command(BaseCommand):
    help = "Enable MCP Tier-2 scanning for an org (and optionally set a server's scan action)."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="zeroshield", help="Organization slug")
        parser.add_argument(
            "--strict-mode", default="fail_open", choices=["strict", "fail_open"],
            help="Tier-2 outage behaviour (default fail_open: degrade to Tier-1 if Bedrock is down)",
        )
        parser.add_argument("--direction", default="both", choices=["input", "output", "both"])
        parser.add_argument("--server-slug", default="", help="If set, update this server's default_scan_action")
        parser.add_argument("--scan-action", default="block", choices=["tag", "redact", "block"])

    def handle(self, *args, **opts):
        org = Organization.objects.filter(slug=opts["org"]).first()
        if not org:
            self.stderr.write(self.style.ERROR(f"Org '{opts['org']}' not found"))
            return

        fw, _ = FirewallConfig.objects.get_or_create(organization=org)
        fw.mcp_tier2_enabled = True
        fw.save(update_fields=["mcp_tier2_enabled"])
        self.stdout.write(self.style.SUCCESS(
            f"FirewallConfig.mcp_tier2_enabled=True for org '{org.slug}'"))

        ctrl = MCPScanControl.objects.filter(
            organization=org, server__isnull=True, tier="tier2",
            scope_type="org", direction=opts["direction"],
        ).first()
        if ctrl is None:
            ctrl = MCPScanControl(
                organization=org, server=None, tool_name="", tier="tier2",
                scope_type="org", direction=opts["direction"],
            )
        ctrl.enabled = True
        ctrl.strict_mode = opts["strict_mode"]
        ctrl.target_mode = "entire"
        ctrl.priority = 100
        ctrl.save()
        self.stdout.write(self.style.SUCCESS(
            f"MCPScanControl tier2 ({opts['direction']}, strict_mode={opts['strict_mode']}, "
            f"enabled=True) ready for org '{org.slug}'"))

        slug = opts["server_slug"]
        if slug:
            srv = MCPServerRegistration.objects.filter(organization=org, server_slug=slug).first()
            if srv:
                srv.default_scan_action = opts["scan_action"]
                srv.save(update_fields=["default_scan_action"])
                self.stdout.write(self.style.SUCCESS(
                    f"Server '{srv.server_slug}'.default_scan_action='{opts['scan_action']}'"))
            else:
                self.stderr.write(self.style.WARNING(f"Server '{slug}' not found for org '{org.slug}'"))
