"""Phase B: BYOK auth credentials + sync diagnostics on MCPServerRegistration.

Secret values (auth_token / auth_username / auth_password / auth_header_value)
use policy.encrypted_fields.EncryptedCharField — transparent Fernet encryption
at rest ("enc:" prefix). The DB column type is plain varchar, so these AddField
operations are metadata-only at the SQL level (constant defaults, PG11+ adds
the column without a table rewrite). auth_header_key is a header *name* (not a
secret) so it stays a plain CharField.
"""
from django.db import migrations, models

import policy.encrypted_fields


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_connector", "0007_presidio_phase1"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpserverregistration",
            name="auth_type",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("none", "None"),
                    ("bearer", "Bearer token"),
                    ("basic", "Basic auth"),
                    ("authheaders", "Custom header"),
                    ("query_param", "Query parameter"),
                ],
                default="none",
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="auth_token",
            field=policy.encrypted_fields.EncryptedCharField(
                blank=True, default="", max_length=2048
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="auth_username",
            field=policy.encrypted_fields.EncryptedCharField(
                blank=True, default="", max_length=512
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="auth_password",
            field=policy.encrypted_fields.EncryptedCharField(
                blank=True, default="", max_length=2048
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="auth_header_key",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="auth_header_value",
            field=policy.encrypted_fields.EncryptedCharField(
                blank=True, default="", max_length=2048
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="last_sync_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="last_sync_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
