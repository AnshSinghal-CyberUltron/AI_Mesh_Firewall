"""
Management command to bulk-load policies from a JSON file.

Usage:
    python manage.py load_policies --file policy/fixtures/policies_500.json
    python manage.py load_policies --file path/to/policies.json --organization zeroshield
    python manage.py load_policies --file path/to/policies.json --dry-run
    python manage.py load_policies --file path/to/policies.json --skip-existing
    python manage.py load_policies --file path/to/policies.json --no-compile

JSON format:
    Array of policy objects. Each policy:
    - name (required), code (required, unique slug)
    - category, severity (CRITICAL|HIGH|MEDIUM|LOW), description, enabled, priority, metadata
    - rules (optional): array of rule objects
    Each rule: name, rule_type (regex|keywords|pattern), condition (JSON), action (block|redact|monitor),
    redaction_config (optional), priority, enabled, description
"""

import json
import logging
import os
import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
VALID_RULE_TYPES = {"regex", "keywords", "pattern"}
VALID_ACTIONS = {"block", "redact", "monitor"}


class Command(BaseCommand):
    help = "Load policies and rules from a JSON file."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--file",
            type=str,
            required=True,
            help="Path to JSON file containing policy definitions.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report what would be done without writing to the database.",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Skip policies whose code already exists (do not update).",
        )
        parser.add_argument(
            "--no-compile",
            action="store_true",
            help="Do not run policy compilation after loading.",
        )
        parser.add_argument(
            "--organization",
            type=str,
            default=None,
            help="Organization slug (e.g. zeroshield). Loaded policies will be assigned to this org only.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        file_path = options["file"]
        dry_run = options["dry_run"]
        skip_existing = options["skip_existing"]
        no_compile = options["no_compile"]
        org_slug = options.get("organization")

        if not os.path.isfile(file_path):
            raise CommandError(f"File not found: {file_path}")

        with open(file_path, encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON: {e}") from e

        if isinstance(data, dict) and "policies" in data:
            policies_data = data["policies"]
        elif isinstance(data, list):
            policies_data = data
        else:
            raise CommandError("JSON must be an array of policy objects or {policies: [...]}")

        self._validate_policies(policies_data)

        org = None
        if org_slug:
            from auth.models import Organization

            org = Organization.objects.filter(slug=org_slug).first()
            if not org:
                raise CommandError(
                    f"Organization with slug '{org_slug}' not found. "
                    "Create it in Django admin or use an existing slug."
                )
            self.stdout.write(f"Assigning policies to organization: {org.name} (slug={org_slug})")


        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"Dry run: would load {len(policies_data)} policies.")
            )
            total_rules = sum(len(p.get("rules", [])) for p in policies_data)
            self.stdout.write(f"Total rules: {total_rules}")
            return

        from policy.models import Policy, Rule

        created_count = 0
        updated_count = 0
        skipped_count = 0
        rules_created = 0
        batch_size = 200

        for item in policies_data:
            code = item.get("code")
            if not code:
                self.stderr.write(self.style.WARNING("Skipping policy with empty code"))
                continue

            defaults = {
                "name": item.get("name", ""),
                "category": item.get("category", ""),
                "severity": item.get("severity", "MEDIUM"),
                "description": item.get("description", ""),
                "enabled": item.get("enabled", True),
                "priority": item.get("priority", 0),
                "metadata": item.get("metadata", {}),
            }
            if org is not None:
                defaults["organization_id"] = org.id
            if defaults["severity"] not in VALID_SEVERITIES:
                defaults["severity"] = "MEDIUM"

            if skip_existing and Policy.objects.filter(code=code).exists():
                skipped_count += 1
                continue

            policy, created = Policy.objects.update_or_create(
                code=code,
                defaults=defaults,
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

            # Replace rules for this policy
            rules_data = item.get("rules", [])
            if rules_data:
                Rule.objects.filter(policy=policy).delete()
                rule_objects = []
                for r in rules_data:
                    rule_type = r.get("rule_type", "keywords")
                    if rule_type not in VALID_RULE_TYPES:
                        rule_type = "keywords"
                    action = r.get("action", "block")
                    if action not in VALID_ACTIONS:
                        action = "block"
                    rule_objects.append(
                        Rule(
                            policy=policy,
                            name=r.get("name", ""),
                            rule_type=rule_type,
                            condition=r.get("condition", {}),
                            action=action,
                            redaction_config=r.get("redaction_config", {}),
                            priority=r.get("priority", 0),
                            enabled=r.get("enabled", True),
                            description=r.get("description", ""),
                        )
                    )
                for i in range(0, len(rule_objects), batch_size):
                    batch = rule_objects[i : i + batch_size]
                    Rule.objects.bulk_create(batch)
                    rules_created += len(batch)

        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded: {created_count} created, {updated_count} updated, {skipped_count} skipped. Rules: {rules_created}"
            )
        )

        if not no_compile:
            self.stdout.write("Compiling policies...")
            from policy.compiler import PolicyCompiler

            compiler = PolicyCompiler()
            bundle = compiler.compile_all()
            success = compiler.push_to_redis(
                bundle,
                trigger="load_policies",
                changed_policy_ids=[],
            )
            if success:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Compiled and pushed {bundle['policy_count']} policies to Redis."
                    )
                )
            else:
                self.stderr.write(
                    self.style.WARNING("Compilation succeeded but Redis push failed.")
                )
                sys.exit(1)

    def _validate_policies(self, policies_data: list) -> None:
        """Validate policy structure. Raises CommandError on invalid data."""
        for i, item in enumerate(policies_data):
            if not isinstance(item, dict):
                raise CommandError(f"Policy at index {i} must be an object")
            if not item.get("name"):
                raise CommandError(f"Policy at index {i}: 'name' is required")
            if not item.get("code"):
                raise CommandError(f"Policy at index {i}: 'code' is required")
            severity = item.get("severity", "MEDIUM")
            if severity not in VALID_SEVERITIES:
                raise CommandError(
                    f"Policy at index {i}: severity must be one of {VALID_SEVERITIES}"
                )
            for j, r in enumerate(item.get("rules", [])):
                if not isinstance(r, dict):
                    raise CommandError(
                        f"Policy {item.get('code')} rule at index {j} must be an object"
                    )
                if not r.get("name"):
                    raise CommandError(
                        f"Policy {item.get('code')} rule at index {j}: 'name' is required"
                    )
                rt = r.get("rule_type", "keywords")
                if rt not in VALID_RULE_TYPES:
                    raise CommandError(
                        f"Policy {item.get('code')} rule at index {j}: "
                        f"rule_type must be one of {VALID_RULE_TYPES}"
                    )
                cond = r.get("condition", {})
                if not isinstance(cond, dict):
                    raise CommandError(
                        f"Policy {item.get('code')} rule at index {j}: condition must be object"
                    )
                if rt == "regex" and not (cond.get("regex") or cond.get("pattern")):
                    raise CommandError(
                        f"Policy {item.get('code')} rule at index {j}: "
                        "regex/pattern rule requires 'regex' or 'pattern' in condition"
                    )
                if rt == "keywords" and "keywords" not in cond and "keywords_list" not in cond:
                    raise CommandError(
                        f"Policy {item.get('code')} rule at index {j}: "
                        "keywords rule requires 'keywords' or 'keywords_list' in condition"
                    )
                action = r.get("action", "block")
                if action not in VALID_ACTIONS:
                    raise CommandError(
                        f"Policy {item.get('code')} rule at index {j}: "
                        f"action must be one of {VALID_ACTIONS}"
                    )
