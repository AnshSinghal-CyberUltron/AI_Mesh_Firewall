from django.db import migrations, models


def migrate_aiguardx_source(apps, schema_editor):
    PocSubmission = apps.get_model("core", "PocSubmission")
    PocSubmission.objects.filter(source="aiguardx").update(source="ai-mesh")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_firewallconfig_hallucination_grounding"),
    ]

    operations = [
        migrations.RunPython(migrate_aiguardx_source, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name="pocsubmission",
            name="source",
            field=models.CharField(
                choices=[("ai-mesh", "AI Mesh Firewall")],
                db_index=True,
                default="ai-mesh",
                max_length=50,
            ),
        ),
    ]
