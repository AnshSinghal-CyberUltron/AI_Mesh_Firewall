from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_firewallconfig_mcp_tier2_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="gatewayapikey",
            name="encrypted_secret",
            field=models.TextField(blank=True, default=""),
        ),
    ]
