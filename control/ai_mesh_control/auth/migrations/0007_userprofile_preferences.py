from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0006_unique_user_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="preferences",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
