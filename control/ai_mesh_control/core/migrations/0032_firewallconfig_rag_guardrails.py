from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0031_alter_firewallconfig_output_hallucination_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="rag_redaction_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Redact sensitive values with vector-safe typed placeholders "
                    "([EMAIL], [SSN], [CREDIT_CARD], ...) before embedding RAG "
                    "documents at ingestion. Preserves semantics for vector matching."
                ),
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="rag_tier2_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Run the ML Tier-2 guard model (boto3 Bedrock) on RAG ingestion "
                    "documents and vector queries, in addition to static Tier-1 scanning."
                ),
            ),
        ),
    ]
