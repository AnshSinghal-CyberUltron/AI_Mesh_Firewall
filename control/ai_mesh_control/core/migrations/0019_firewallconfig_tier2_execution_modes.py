from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_add_source_to_poc_submission"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="tier2_execution_mode",
            field=models.CharField(
                choices=[("sync_pre_llm", "Sync Pre-LLM"), ("async_post_llm", "Async Post-LLM")],
                default="sync_pre_llm",
                max_length=32,
                help_text="Tier-2 execution strategy for Bedrock scanning.",
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="tier2_stream_hold_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Allow stream-hold mode before first token when async Tier-2 is configured.",
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="tier2_stream_hold_timeout_ms",
            field=models.PositiveIntegerField(
                default=1200,
                validators=[MinValueValidator(500), MaxValueValidator(2000)],
                help_text="Max pre-stream hold duration in milliseconds for Tier-2 block mode.",
            ),
        ),
    ]
