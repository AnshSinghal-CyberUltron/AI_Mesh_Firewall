"""
Create or update the ZeroShield organization admin login for AI Mesh Firewall.

Usage (from AI_Mesh_Firewall/):
  docker compose exec control python manage.py ensure_zeroshield_admin

Environment (optional):
  ZEROSHIELD_ADMIN_EMAIL     default: admin@zeroshield.io
  ZEROSHIELD_ADMIN_PASSWORD  required in production; dev default if unset
  ZEROSHIELD_ORG_SLUG        default: zeroshield
  ZEROSHIELD_ORG_NAME        default: ZeroShield
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Create ZeroShield org + platform admin user for console login"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            default="",
            help="Admin email (default: ZEROSHIELD_ADMIN_EMAIL or admin@zeroshield.io)",
        )
        parser.add_argument(
            "--password",
            type=str,
            default="",
            help="Admin password (default: ZEROSHIELD_ADMIN_PASSWORD env)",
        )
        parser.add_argument(
            "--org-slug",
            type=str,
            default="",
            help="Organization slug (default: zeroshield)",
        )
        parser.add_argument(
            "--org-name",
            type=str,
            default="",
            help="Organization display name (default: ZeroShield)",
        )
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Update password when the user already exists",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        from auth.models import Organization, Role, UserProfile
        from core.models import FirewallConfig

        email = (
            options["email"]
            or os.getenv("ZEROSHIELD_ADMIN_EMAIL", "admin@zeroshield.io")
        ).strip().lower()
        password = options["password"] or os.getenv("ZEROSHIELD_ADMIN_PASSWORD", "").strip()
        org_slug = (
            options["org_slug"] or os.getenv("ZEROSHIELD_ORG_SLUG", "zeroshield")
        ).strip()
        org_name = (
            options["org_name"] or os.getenv("ZEROSHIELD_ORG_NAME", "ZeroShield")
        ).strip()

        if not password:
            password = "Adm1n!Pass#2024"
            self.stdout.write(
                self.style.WARNING(
                    "No password supplied; using dev default. "
                    "Set ZEROSHIELD_ADMIN_PASSWORD or pass --password."
                )
            )

        org, org_created = Organization.objects.get_or_create(
            slug=org_slug,
            defaults={"name": org_name, "is_active": True},
        )
        if not org_created and org.name != org_name:
            org.name = org_name
            org.save(update_fields=["name"])

        User = get_user_model()
        user = User.objects.filter(email__iexact=email).first()
        user_created = False
        if user is None:
            username = email.split("@")[0]
            base_username = username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_staff=True,
                is_superuser=True,
            )
            user_created = True
        else:
            if options["reset_password"]:
                user.set_password(password)
            user.is_staff = True
            user.is_superuser = True
            user.is_active = True
            user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if profile.organization_id != org.id:
            profile.organization = org
            profile.save(update_fields=["organization"])

        admin_role, _ = Role.objects.get_or_create(
            name="platform_admin",
            defaults={"description": "Administrator for Offering 1 (Dashboard + Modules 1–4)"},
        )
        profile.roles.add(admin_role)

        FirewallConfig.load(organization=org)

        try:
            from core.simulator_seed import ensure_simulator_dev_bootstrap

            ensure_simulator_dev_bootstrap(org)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"Simulator bootstrap skipped: {exc}"))

        self.stdout.write(self.style.SUCCESS("ZeroShield admin login ready"))
        self.stdout.write(f"  Organization : {org.name} (slug={org.slug})")
        self.stdout.write(f"  Email        : {email}")
        self.stdout.write(f"  User created : {user_created}")
        self.stdout.write(f"  Password set : {user_created or options['reset_password']}")
        self.stdout.write("  Login        : POST /api/auth/token/ with email + password")
        self.stdout.write("  UI           : http://127.0.0.1:8180/login")
