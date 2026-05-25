"""Data migration: seed the four offering-scoped RBAC roles."""

from django.db import migrations

ROLES = [
    ("platform_admin", "Administrator for Offering 1 (Dashboard + Modules 1–4)"),
    ("platform_user", "Normal user for Offering 1 (Dashboard + Modules 1–4)"),
    ("aiguardx_admin", "Administrator for Offering 2 (AIGuardX / Module 5)"),
    ("aiguardx_user", "Normal user for Offering 2 (AIGuardX / Module 5)"),
]


def seed_roles(apps, schema_editor):
    Role = apps.get_model("auth_api", "Role")
    for name, description in ROLES:
        Role.objects.get_or_create(name=name, defaults={"description": description})


def remove_roles(apps, schema_editor):
    Role = apps.get_model("auth_api", "Role")
    Role.objects.filter(name__in=[r[0] for r in ROLES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0002_terminatedsession"),
    ]

    operations = [
        migrations.RunPython(seed_roles, reverse_code=remove_roles),
    ]
