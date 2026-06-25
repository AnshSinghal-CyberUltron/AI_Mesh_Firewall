# Hand-written: secure-by-default for the per-org RAG redaction flag (FIX G2a).
# Existing rows keep their stored value (no data migration — separate product decision).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0034_remove_firewallconfig_alert_recipients_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='firewallconfig',
            name='rag_redaction_enabled',
            field=models.BooleanField(default=True, help_text='Redact sensitive values with vector-safe typed placeholders ([EMAIL], [SSN], [CREDIT_CARD], ...) before embedding RAG documents at ingestion. Preserves semantics for vector matching.'),
        ),
    ]
