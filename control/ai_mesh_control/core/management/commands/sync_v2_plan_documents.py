"""Recompile every active organization's gateway-v2 plan document (GW36 S2).

Run once on deploy (backfill: an org that never saved since S2 has no document, and the
projector publishes no PLAN record for it), and whenever rules changed outside the console
views -- seed commands and bulk ``QuerySet.update()`` emit no signal.

``--purge-legacy-plan-keys`` deletes the unsigned documents control wrote to ``{rv2}:plan:*``
before S2. Only raw documents are removed; a signed feed envelope is never touched.

``--if-changed`` is the deploy step (F4; ``core/v2_plan_rollout.py``): it sweeps every active org
only when what the publisher writes changed since the last complete sweep (a release, or a change
to ``V2_PLAN_NEW_RULES_MODE`` or another publish setting), and records the new fingerprint.
``control/server-entrypoint.sh`` runs it in the background on every container start.
"""

from __future__ import annotations

import json
from typing import Any

from core.firewall_v2_publish import (
    LEGACY_PLAN_PATTERN,
    is_raw_document,
    publish_for_org,
)
from core.v2_plan_rollout import reconcile_until_settled
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Recompile and store gateway-v2 plan documents for active organizations."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--org-slug", help="Only this organization.")
        parser.add_argument(
            "--purge-legacy-plan-keys",
            action="store_true",
            help="Delete pre-GW36 unsigned documents under {rv2}:plan:* (raw documents only).",
        )
        parser.add_argument(
            "--if-changed",
            action="store_true",
            help="Sweep every active org only if the publisher fingerprint changed (deploy step).",
        )
        parser.add_argument(
            "--attempts", type=int, default=1, help="With --if-changed: tries before giving up.",
        )
        parser.add_argument(
            "--wait", type=float, default=30.0, help="With --if-changed: seconds between tries.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options.get("if_changed"):
            self._if_changed(options)
            return
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

    def _if_changed(self, options: dict[str, Any]) -> None:
        if options.get("org_slug") or options.get("purge_legacy_plan_keys"):
            raise CommandError("--if-changed sweeps every org; it takes no other option")
        note = reconcile_until_settled(attempts=options["attempts"], wait_s=options["wait"])
        self.stdout.write(f"gateway-v2 plan rollout: {json.dumps(note, sort_keys=True)}")
        if note.get("status") not in ("current", "swept"):
            raise CommandError(f"gateway-v2 plan rollout did not settle: {note.get('status')}")

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
