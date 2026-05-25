"""
Migration: Add structured MCP policy models.
Auto-generated migration for MCPStructuredPolicy, MCPPolicyAudit, MCPRiskComponent.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('policy', '0017_vector_provider_config'),  # Latest migration before this
        ('auth_api', '0005_backfill_default_organization'),
    ]

    operations = [
        # MCPStructuredPolicy model
        migrations.CreateModel(
            name='MCPStructuredPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=256)),
                ('policy_type', models.CharField(choices=[('tool_control', 'Tool Control (allow/block specific tools)'), ('argument_constraint', 'Argument Constraint (type, pattern, range validation)'), ('context_constraint', 'Context Constraint (user role, org, session-based)'), ('rate_limit', 'Rate Limit (per-user, per-org, per-tool rate limiting)'), ('risk_policy', 'Risk Policy (low/medium/high/critical risk scoring)')], max_length=50)),
                ('description', models.TextField(blank=True, default='')),
                ('config', models.JSONField(default=dict)),
                ('enabled', models.BooleanField(default=True)),
                ('priority', models.IntegerField(default=100, help_text='Higher = evaluated first')),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mcp_structured_policies', to='auth_api.organization')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_mcp_policies', to='auth.user')),
            ],
            options={
                'verbose_name_plural': 'MCP Structured Policies',
                'ordering': ['-priority', '-updated_at'],
            },
        ),
        
        # MCPPolicyAudit model
        migrations.CreateModel(
            name='MCPPolicyAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_id', models.CharField(blank=True, max_length=256, null=True)),
                ('tool_name', models.CharField(db_index=True, max_length=256)),
                ('tool_arguments', models.JSONField(default=dict)),
                ('matched_policies', models.JSONField(default=list, help_text='List of policy IDs/names that matched')),
                ('action', models.CharField(choices=[('allow', 'Allow'), ('block', 'Block'), ('redact', 'Redact'), ('audit', 'Audit Only')], max_length=32)),
                ('risk_score', models.FloatField(default=0.0, help_text='0-100 risk score')),
                ('reason', models.TextField(blank=True, default='')),
                ('request_id', models.CharField(db_index=True, max_length=256)),
                ('user_ip', models.CharField(blank=True, max_length=255)),
                ('session_id', models.CharField(blank=True, max_length=256)),
                ('redacted_fields', models.JSONField(blank=True, default=list)),
                ('timestamp', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='auth_api.organization')),
            ],
            options={
                'verbose_name': 'MCP Policy Audit',
                'verbose_name_plural': 'MCP Policy Audits',
                'ordering': ['-timestamp'],
            },
        ),
        
        # MCPRiskComponent model
        migrations.CreateModel(
            name='MCPRiskComponent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('component_type', models.CharField(choices=[('tool_sensitivity', 'Tool Sensitivity (base risk per tool)'), ('argument_analysis', 'Argument Content Analysis (keywords → risk delta)'), ('user_role', 'User Role (role → risk modifier)'), ('user_history', 'User History (repeated violations → escalation)')], max_length=50)),
                ('tool_name', models.CharField(blank=True, db_index=True, max_length=256)),
                ('tool_sensitivity_score', models.FloatField(default=0.0)),
                ('keywords', models.JSONField(default=dict)),
                ('user_role', models.CharField(blank=True, max_length=50)),
                ('role_score_modifier', models.FloatField(default=1.0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='auth_api.organization')),
            ],
            options={
                'verbose_name': 'MCP Risk Component',
                'verbose_name_plural': 'MCP Risk Components',
                'ordering': ['-tool_sensitivity_score'],
            },
        ),
        
        # Add indexes
        migrations.AddIndex(
            model_name='mcpstructuredpolicy',
            index=models.Index(fields=['organization', 'enabled', '-priority'], name='policy_mcp_org_enabled_priority'),
        ),
        migrations.AddIndex(
            model_name='mcpstructuredpolicy',
            index=models.Index(fields=['organization', 'policy_type'], name='policy_mcp_org_type'),
        ),
        migrations.AddIndex(
            model_name='mcppolicyaudit',
            index=models.Index(fields=['organization', '-timestamp'], name='policy_mcp_audit_org_ts'),
        ),
        migrations.AddIndex(
            model_name='mcppolicyaudit',
            index=models.Index(fields=['request_id'], name='policy_mcp_audit_req_id'),
        ),
        migrations.AddIndex(
            model_name='mcppolicyaudit',
            index=models.Index(fields=['action', 'timestamp'], name='policy_mcp_audit_action_ts'),
        ),
    ]
