from django.db import migrations, models

OUTPUT_GUARD_ACTION_CHOICES = [
    ("block", "Block"),
    ("redact", "Redact"),
    ("rewrite", "Rewrite"),
    ("flag", "Flag"),
    ("allow", "Allow"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_firewallconfig_governance_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="firewallconfig",
            name="output_pii_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Detect PII / personal-data leakage in model responses.",
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="output_pii_action",
            field=models.CharField(
                choices=OUTPUT_GUARD_ACTION_CHOICES,
                default="redact",
                help_text="Action applied when PII is detected in a response.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="output_credential_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Detect credential / secret exposure in model responses.",
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="output_credential_action",
            field=models.CharField(
                choices=OUTPUT_GUARD_ACTION_CHOICES,
                default="block",
                help_text="Action applied when credentials/secrets are detected.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="output_ip_leakage_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Detect intellectual-property / infrastructure leakage in responses.",
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="output_ip_leakage_action",
            field=models.CharField(
                choices=OUTPUT_GUARD_ACTION_CHOICES,
                default="flag",
                help_text="Action applied when IP/infrastructure leakage is detected.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="output_policy_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Enforce policy-engine verdicts on the output (post-LLM) path.",
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="output_policy_action",
            field=models.CharField(
                choices=OUTPUT_GUARD_ACTION_CHOICES,
                default="block",
                help_text="Action applied when an output policy violation is detected.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="output_hallucination_action",
            field=models.CharField(
                choices=OUTPUT_GUARD_ACTION_CHOICES,
                default="flag",
                help_text=(
                    "Action applied when hallucination risk is detected. Enable toggle "
                    "is factuality_check_enabled; threshold is "
                    "hallucination_grounding_threshold."
                ),
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="firewallconfig",
            name="output_incident_logging_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Log security incidents (telemetry + audit) for output-guard actions.",
            ),
        ),
    ]
