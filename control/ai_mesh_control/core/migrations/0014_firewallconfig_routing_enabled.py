from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_firewallconfig_routing_weights"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="routing_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Enable Bedrock-adjudicated dynamic model routing for /v1/chat/completions.",
            ),
        ),
    ]
