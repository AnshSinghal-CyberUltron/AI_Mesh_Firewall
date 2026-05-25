from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0014_enforcementevent_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="rule",
            name="pipeline_stage",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "All Stages"),
                    ("query", "Query Stage"),
                    ("retriever", "Retriever Stage"),
                    ("ranker", "Ranker Stage"),
                    ("generator", "Generator Stage"),
                ],
                db_index=True,
                default="",
                help_text="Pipeline stage this rule targets (empty = all stages)",
                max_length=16,
            ),
        ),
    ]
