# Create default organization and assign all existing users to it

from django.db import migrations


def create_default_org_and_backfill(apps, schema_editor):
    Organization = apps.get_model("auth_api", "Organization")
    UserProfile = apps.get_model("auth_api", "UserProfile")
    default_org, _ = Organization.objects.get_or_create(
        slug="default",
        defaults={"name": "Default", "is_active": True},
    )
    UserProfile.objects.filter(organization__isnull=True).update(organization_id=default_org.id)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0004_organization_and_userprofile_organization"),
    ]

    operations = [
        migrations.RunPython(create_default_org_and_backfill, reverse_code=noop_reverse),
    ]
