"""Resync KillSwitch rows to Redis and optionally prune orphan keys."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from core.models import KillSwitch
from core.signals import _get_redis_client


class Command(BaseCommand):
    help = (
        "Push all KillSwitch rows to Redis (active=SET, inactive=DELETE) "
        "and optionally remove orphan kill_switch:* keys."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune-orphans",
            action="store_true",
            help="Delete Redis kill_switch:* keys not owned by an active DB row.",
        )
        parser.add_argument(
            "--validate-slugs",
            action="store_true",
            help="Warn when Redis key org prefix differs from payload org_slug.",
        )

    def handle(self, *args, **options):
        client = _get_redis_client()
        active_keys: set[str] = set()
        synced = 0
        slug_warnings = 0

        for ks in KillSwitch.objects.select_related("organization").order_by("id"):
            key = ks.build_redis_key()
            payload = json.dumps(ks.build_redis_payload())
            if ks.is_active:
                client.set(key, payload)
                active_keys.add(key)
                synced += 1
                self.stdout.write(f"SET {key}")
            else:
                client.delete(key)
                self.stdout.write(f"DEL {key} (inactive)")

        if options["validate_slugs"]:
            for key in client.scan_iter("kill_switch:*"):
                key_str = key.decode() if isinstance(key, bytes) else str(key)
                raw = client.get(key)
                if not raw:
                    continue
                try:
                    data = json.loads(raw if isinstance(raw, str) else raw.decode())
                except json.JSONDecodeError:
                    continue
                payload_slug = str(data.get("org_slug") or "").strip()
                key_parts = key_str.split(":")
                key_slug = key_parts[1] if len(key_parts) > 1 else ""
                if payload_slug and key_slug and payload_slug != key_slug:
                    slug_warnings += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"SLUG MISMATCH key={key_str} payload.org_slug={payload_slug}"
                        )
                    )

        pruned = 0
        if options["prune_orphans"]:
            for key in client.scan_iter("kill_switch:*"):
                if key not in active_keys:
                    client.delete(key)
                    pruned += 1
                    self.stdout.write(f"PRUNE {key}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Resync complete: {synced} active, pruned {pruned} orphan key(s), "
                f"{slug_warnings} slug warning(s)."
            )
        )
