"""Add policy_domain field to Policy for strict domain isolation."""

from django.db import migrations, models


def forward_assign_domains(apps, schema_editor):
    """Best-effort assignment for existing rows based on category/name heuristics."""
    Policy = apps.get_model("policy", "Policy")
    import re

    for policy in Policy.objects.all():
        haystack = " ".join(
            filter(None, [policy.name, policy.code, policy.category, policy.description])
        ).lower()
        # Check metadata first (legacy policy_scope)
        scope = (policy.metadata or {}).get("policy_scope", "")
        if scope == "mcp" or scope == "pcm":
            policy.policy_domain = "mcp"
        elif scope == "rag":
            policy.policy_domain = "rag"
        elif scope == "pipeline":
            policy.policy_domain = "pipeline"
        elif scope == "global":
            policy.policy_domain = "global"
        # Heuristic fallback from content
        elif re.search(r"\b(mcp|pcm|tool\s+scope|context\s+assembl)", haystack):
            policy.policy_domain = "mcp"
        elif re.search(r"\b(rag|retriev|vector|embed|rerank|generator|hallucin)", haystack):
            policy.policy_domain = "rag"
        elif re.search(r"\b(pipeline|stage|workflow|orchestrat|routing|multi.model)", haystack):
            policy.policy_domain = "pipeline"
        else:
            policy.policy_domain = "global"
        policy.save(update_fields=["policy_domain"])


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0015_rule_pipeline_stage"),
    ]

    operations = [
        migrations.AddField(
            model_name="policy",
            name="policy_domain",
            field=models.CharField(
                choices=[
                    ("global", "Global"),
                    ("pipeline", "Pipeline"),
                    ("rag", "RAG"),
                    ("mcp", "MCP"),
                ],
                db_index=True,
                default="global",
                help_text="Enforcement domain: global (all requests), pipeline, rag, or mcp",
                max_length=16,
            ),
        ),
        migrations.RunPython(forward_assign_domains, migrations.RunPython.noop),
    ]
