"""GW32 (DD-4): bind the gateway-run upstream OAuth flow state to a key and an expiry."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mcp_connector", "0018_mcpserverregistration_credential_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_flow_key_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_flow_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_token_obtained_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
