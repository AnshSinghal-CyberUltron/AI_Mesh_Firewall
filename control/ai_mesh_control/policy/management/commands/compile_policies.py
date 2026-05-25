"""
Management command to compile policies and push to Redis.

Usage:
    python manage.py compile_policies           # Full compile + push
    python manage.py compile_policies --dry-run # Compile, print JSON, no push
    python manage.py compile_policies --status  # Show current Redis state
"""

import json
import logging
import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Compile all enabled policies into a JSON bundle and push to Redis."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compile and print the bundle JSON without pushing to Redis.",
        )
        parser.add_argument(
            "--status",
            action="store_true",
            help="Display the current compiled policy state from Redis.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from policy.compiler import (
            REDIS_KEY_COMPILED,
            REDIS_KEY_VERSION,
            PolicyCompiler,
            _get_redis_client,
        )

        if options["status"]:
            self._show_status(REDIS_KEY_COMPILED, REDIS_KEY_VERSION, _get_redis_client)
            return

        compiler = PolicyCompiler()
        bundle = compiler.compile_all()
        self.stdout.write(f"Compiled {bundle['policy_count']} enabled policies.")

        if options["dry_run"]:
            self.stdout.write(json.dumps(bundle, indent=2, default=str))
            self.stdout.write(self.style.SUCCESS("Dry run complete. Nothing pushed to Redis."))
            return

        success = compiler.push_to_redis(
            bundle,
            trigger="manual",
            changed_policy_ids=[],
        )

        if success:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Pushed to Redis (version={bundle.get('version')}, policies={bundle['policy_count']})"
                )
            )
        else:
            self.stderr.write(self.style.ERROR("Failed to push compiled policies to Redis."))
            sys.exit(1)

    def _show_status(
        self,
        key_compiled: str,
        key_version: str,
        get_client_fn: Any,
    ) -> None:
        try:
            client = get_client_fn()
            version = client.get(key_version)
            raw_bundle = client.get(key_compiled)

            if raw_bundle is None:
                self.stdout.write(self.style.WARNING("No compiled bundle found in Redis."))
                return

            bundle = json.loads(raw_bundle)
            self.stdout.write(f"Version:      {version}")
            self.stdout.write(f"Policy count: {bundle.get('policy_count', 'N/A')}")
            self.stdout.write(f"Compiled at:  {bundle.get('compiled_at', 'N/A')}")
            self.stdout.write(f"Bundle version: {bundle.get('version', 'N/A')}")

            for entry in bundle.get("policies", []):
                p = entry.get("policy", {})
                rule_count = len(entry.get("rules", []))
                self.stdout.write(
                    f"  [{p.get('code')}] {p.get('name')} (severity={p.get('severity')}, rules={rule_count})"
                )

        except Exception as exc:
            raise CommandError(f"Failed to read from Redis: {exc}") from exc
