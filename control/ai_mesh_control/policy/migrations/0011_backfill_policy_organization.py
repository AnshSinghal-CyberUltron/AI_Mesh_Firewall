# Backfill Policy.organization_id with default organization

from django.db import migrations


def backfill_policy_org(apps, schema_editor):
    Organization = apps.get_model("auth_api", "Organization")
    Policy = apps.get_model("policy", "Policy")
    default_org = Organization.objects.filter(slug="default").first()
    if default_org:
        Policy.objects.filter(organization__isnull=True).update(organization_id=default_org.id)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0005_backfill_default_organization"),
        ("policy", "0010_policy_organization"),
    ]

    operations = [
        migrations.RunPython(backfill_policy_org, reverse_code=noop_reverse),
    ]
