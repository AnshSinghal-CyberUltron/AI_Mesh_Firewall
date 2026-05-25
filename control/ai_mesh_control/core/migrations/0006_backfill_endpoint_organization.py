# Backfill Endpoint.organization_id with default organization

from django.db import migrations


def backfill_endpoint_org(apps, schema_editor):
    Organization = apps.get_model("auth_api", "Organization")
    Endpoint = apps.get_model("core", "Endpoint")
    default_org = Organization.objects.filter(slug="default").first()
    if default_org:
        Endpoint.objects.filter(organization__isnull=True).update(organization_id=default_org.id)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0005_backfill_default_organization"),
        ("core", "0005_endpoint_organization"),
    ]

    operations = [
        migrations.RunPython(backfill_endpoint_org, reverse_code=noop_reverse),
    ]
