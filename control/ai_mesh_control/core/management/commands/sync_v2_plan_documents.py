"""Recompile every active organization's gateway-v2 plan document (GW36 S2).

Run once on deploy (backfill: an org that never saved since S2 has no document, and the
projector publishes no PLAN record for it), and whenever rules changed outside the console
views -- seed commands and bulk ``QuerySet.update()`` emit no signal.

``--purge-legacy-plan-keys`` deletes the unsigned documents control wrote to ``{rv2}:plan:*``
before S2. Only raw documents are removed; a signed feed envelope is never touched.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from core.firewall_v2_publish import LEGACY_PLAN_PATTERN, is_raw_document, publish_for_org


class Command(BaseCommand):
    help = "Recompile and store gateway-v2 plan documents for active organizations."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--org-slug", help="Only this organization.")
        parser.add_argument(
            "--purge-legacy-plan-keys",
            action="store_true",
            help="Delete pre-GW36 unsigned documents under {rv2}:plan:* (raw documents only).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from auth.models import Organization

        orgs = Organization.objects.filter(is_active=True).order_by("slug")
        if options.get("org_slug"):
            orgs = orgs.filter(slug=options["org_slug"])
            if not orgs.exists():
                raise CommandError(f"no active organization {options['org_slug']!r}")
        failed = 0
        for org in orgs:
            note = publish_for_org(org)
            if note.get("status") != "published":
                failed += 1
            self.stdout.write(
                f"{org.slug}: {note.get('status')} revision={note.get('revision')} "
                f"rules={note.get('rules')} dev_copy={note.get('dev_copy')}",
            )
        if options.get("purge_legacy_plan_keys"):
            self.stdout.write(f"purged {self._purge()} legacy plan documents")
        if failed:
            raise CommandError(f"{failed} organization(s) were not stored")

    @staticmethod
    def _purge() -> int:
        import redis
        from django.conf import settings

        client = redis.Redis.from_url(
            settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2,
        )
        removed = 0
        try:
            for name in client.scan_iter(match=LEGACY_PLAN_PATTERN, count=500):
                if is_raw_document(client.get(name)):
                    removed += int(client.delete(name))
        finally:
            client.close()
        return removed
