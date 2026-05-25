from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("policy", "0005_vectorcollectionpolicy"),
    ]

    operations = [
        migrations.AddField(
            model_name="policy",
            name="is_system",
            field=models.BooleanField(
                default=False,
                help_text="System-seeded policy; cannot be deleted via API",
            ),
        ),
    ]
