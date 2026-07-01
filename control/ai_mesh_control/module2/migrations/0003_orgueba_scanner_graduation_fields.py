from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("module2", "0002_ueba_v2_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="orguebasettings",
            name="scanner_graduation_min_days",
            field=models.FloatField(default=1.0),
        ),
        migrations.AddField(
            model_name="orguebasettings",
            name="scanner_graduation_min_requests",
            field=models.PositiveIntegerField(default=10),
        ),
    ]
