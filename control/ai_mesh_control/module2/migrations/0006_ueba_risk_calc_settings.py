from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("module2", "0005_apikeybehaviorprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="orguebasettings",
            name="behavior_profile_prompt_target",
            field=models.PositiveIntegerField(default=50),
        ),
        migrations.AddField(
            model_name="orguebasettings",
            name="weight_baseline_deviation",
            field=models.FloatField(default=0.20),
        ),
        migrations.AddField(
            model_name="orguebasettings",
            name="weight_block_rate",
            field=models.FloatField(default=0.50),
        ),
        migrations.AddField(
            model_name="orguebasettings",
            name="weight_policy_escalation",
            field=models.FloatField(default=0.10),
        ),
        migrations.AddField(
            model_name="orguebasettings",
            name="weight_threat_severity",
            field=models.FloatField(default=0.25),
        ),
        migrations.AddField(
            model_name="orguebasettings",
            name="weight_velocity",
            field=models.FloatField(default=0.15),
        ),
        migrations.AddField(
            model_name="apikeybehaviorprofile",
            name="baseline_metrics",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
