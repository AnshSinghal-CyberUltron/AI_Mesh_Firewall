"""Bulk re-sync MCP server tool discovery and clear stale sync errors.

Operational helper for the Flow-2 / stale-error fixes: after a gateway
rebuild (e.g. npx/node became available) the persisted ``last_sync_error``
values like "Command not found: npx" are stale. This command re-triggers
discovery through the same gateway path the API uses so statuses and errors
are refreshed without clicking each server in the UI.

Examples::

    python manage.py resync_mcp_servers --org=zeroshield
    python manage.py resync_mcp_servers --org=zeroshield --server-slug=linear-mcp
    python manage.py resync_mcp_servers --all
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from auth.models import Organization
from mcp_connector.models import MCPServerRegistration
from mcp_connector.views import _resync_server_tools


class Command(BaseCommand):
    help = "Re-sync MCP server tool discovery (refreshes connection_status / last_sync_error)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            dest="org_slug",
            help="Organization slug to scope the re-sync to.",
        )
        parser.add_argument(
            "--server-slug",
            dest="server_slug",
            help="Only re-sync this server slug (requires --org).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_orgs",
            help="Re-sync every server across every active organization.",
        )

    def handle(self, *args, **options):
        org_slug = options.get("org_slug")
        server_slug = options.get("server_slug")
        all_orgs = options.get("all_orgs")

        if not all_orgs and not org_slug:
            raise CommandError("Provide --org=<slug> or --all.")
        if server_slug and not org_slug:
            raise CommandError("--server-slug requires --org.")

        if all_orgs:
            orgs = list(Organization.objects.filter(is_active=True))
        else:
            org = Organization.objects.filter(slug=org_slug, is_active=True).first()
            if org is None:
                raise CommandError(f"Organization not found or inactive: {org_slug}")
            orgs = [org]

        total = 0
        failures = 0
        for org in orgs:
            servers = MCPServerRegistration.objects.filter(organization=org)
            if server_slug:
                servers = servers.filter(server_slug=server_slug)
            for server in servers:
                total += 1
                try:
                    result = _resync_server_tools(server, org)
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    self.stderr.write(
                        f"[{org.slug}/{server.server_slug}] re-sync raised: {exc}"
                    )
                    continue
                err = result.get("error")
                if err:
                    failures += 1
                    self.stdout.write(
                        f"[{org.slug}/{server.server_slug}] {result['connection_status']} "
                        f"— synced={result['synced']} error={err}"
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[{org.slug}/{server.server_slug}] connected "
                            f"— synced={result['synced']} pruned={result['pruned']}"
                        )
                    )

        if total == 0:
            self.stdout.write("No matching MCP servers found.")
        else:
            self.stdout.write(
                f"Done: {total} server(s) processed, {failures} with errors."
            )
