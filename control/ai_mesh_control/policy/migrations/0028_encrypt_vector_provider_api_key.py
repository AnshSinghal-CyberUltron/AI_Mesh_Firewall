"""
Bundle X3 — encrypt-in-place migration for VectorProviderConfig.api_key.

The Django schema delta for switching ``models.CharField`` →
``policy.encrypted_fields.EncryptedCharField`` is a no-op (same SQL
column type, same max_length), so the only thing this migration does is
load every existing row, run the api_key through Fernet, and save it
back. Rows that are already prefixed with ``enc:`` are left alone, which
makes this migration safely idempotent — running it twice (or running
fakemigrate then re-running) cannot double-encrypt.

Two-phase reasoning:
  * ``AlterField`` records the new field class in Django's migration
    state so subsequent makemigrations does not re-detect a change.
  * ``RunPython`` performs the actual data rewrite, using the historical
    model from ``apps.get_model`` so the migration stays runnable even
    if the live model class changes again later.
"""

from django.db import migrations, models

import policy.encrypted_fields


def encrypt_existing_api_keys(apps, schema_editor):
    VectorProviderConfig = apps.get_model("policy", "VectorProviderConfig")
    # iterator() to keep memory flat if there are many rows; .only() narrows
    # the SELECT to just the fields we touch.
    for row in VectorProviderConfig.objects.only("id", "api_key").iterator():
        value = row.api_key or ""
        if value.startswith("enc:"):
            continue
        # Re-save will trigger EncryptedCharField.get_prep_value and
        # produce the "enc:" ciphertext form on UPDATE.
        row.api_key = value
        row.save(update_fields=["api_key"])


def decrypt_existing_api_keys(apps, schema_editor):
    """
    Reverse migration — decrypts ciphertext back to plaintext. Only useful
    if an operator needs to roll back the field swap (very rare). Uses the
    same Fernet derivation as the forward path.
    """
    VectorProviderConfig = apps.get_model("policy", "VectorProviderConfig")
    for row in VectorProviderConfig.objects.only("id", "api_key").iterator():
        value = row.api_key or ""
        if not value.startswith("enc:"):
            continue
        # The historical model's CharField will store whatever string we
        # assign without re-encrypting, so call the helper directly.
        row.api_key = policy.encrypted_fields.decrypt_value(value)
        row.save(update_fields=["api_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0027_vector_collection_org_unique"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vectorproviderconfig",
            name="api_key",
            field=policy.encrypted_fields.EncryptedCharField(
                blank=True,
                default="",
                help_text=(
                    "Provider API key (Pinecone API key, Milvus token, etc.). "
                    "Encrypted at rest via Fernet (see policy.encrypted_fields). "
                    "Plaintext is only materialised in-process when building the "
                    "Redis bundle for the gateway."
                ),
                max_length=512,
            ),
        ),
        migrations.RunPython(
            encrypt_existing_api_keys,
            reverse_code=decrypt_existing_api_keys,
        ),
    ]
