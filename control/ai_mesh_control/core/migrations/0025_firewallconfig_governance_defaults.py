"""Empty governance defaults + sanitize legacy allowlists."""

from django.db import migrations, models


def _parse_allowed(value):
    if not value:
        return []
    return [m.strip() for m in str(value).split(",") if m.strip()]


def _connected_names_historical(LLMModelConfig, org_id):
    qs = LLMModelConfig.objects.filter(organization_id=org_id, is_active=True)
    names = set()
    for row in qs:
        if str(row.provider or "").lower() == "internal":
            continue
        if row.model_name:
            names.add(row.model_name)
    return names


def sanitize_all_allowlists(apps, schema_editor):
    FirewallConfig = apps.get_model("core", "FirewallConfig")
    LLMModelConfig = apps.get_model("core", "LLMModelConfig")

    for row in FirewallConfig.objects.filter(organization_id__isnull=False):
        org_id = row.organization_id
        allowed = _parse_allowed(row.allowed_models)
        default = (row.default_model or "").strip()
        connected = _connected_names_historical(LLMModelConfig, org_id)

        if not connected:
            new_allowed = ""
            new_default = ""
        else:
            cleaned = [m for m in allowed if m in connected]
            new_allowed = ", ".join(cleaned)
            if default and default in connected and (not cleaned or default in cleaned):
                new_default = default
            elif cleaned:
                new_default = cleaned[0]
            else:
                new_default = ""

        if new_allowed == (row.allowed_models or "") and new_default == default:
            continue

        row.allowed_models = new_allowed
        row.default_model = new_default
        row.save(update_fields=["allowed_models", "default_model"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_killswitch_api_key_prefix"),
    ]

    operations = [
        migrations.AlterField(
            model_name="firewallconfig",
            name="allowed_models",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Comma-separated list of approved models.",
            ),
        ),
        migrations.AlterField(
            model_name="firewallconfig",
            name="default_model",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Fallback model when none specified.",
                max_length=64,
            ),
        ),
        migrations.RunPython(sanitize_all_allowlists, migrations.RunPython.noop),
    ]
