from django.db import migrations, models


ATTACK_VAULT_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS attack_vault_embeddings (
    attack_id TEXT PRIMARY KEY,
    attack_text TEXT NOT NULL,
    attack_type TEXT NOT NULL,
    embedding VECTOR(1536) NOT NULL,
    source TEXT NOT NULL DEFAULT 'seed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attack_vault_created_at
    ON attack_vault_embeddings (created_at DESC);
"""


ROLLBACK_ATTACK_VAULT_SQL = """
DROP TABLE IF EXISTS attack_vault_embeddings;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0020_add_target_tool_to_rule"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vectorcollectionpolicy",
            name="vector_db_type",
            field=models.CharField(
                choices=[("pinecone", "Pinecone"), ("milvus", "Milvus"), ("custom", "Custom")],
                default="pinecone",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="vectorproviderconfig",
            name="provider_type",
            field=models.CharField(
                choices=[("pinecone", "Pinecone"), ("milvus", "Milvus"), ("custom", "Custom")],
                help_text="Vector database provider (pinecone, milvus, custom).",
                max_length=32,
            ),
        ),
        migrations.RunSQL(
            sql=ATTACK_VAULT_SQL,
            reverse_sql=ROLLBACK_ATTACK_VAULT_SQL,
        ),
    ]
