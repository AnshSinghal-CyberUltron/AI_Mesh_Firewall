from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("auth_api", "0005_backfill_default_organization"),
    ]

    operations = [
        migrations.RunSQL(
            # Add a case-insensitive unique index on email (skip blank)
            sql="CREATE UNIQUE INDEX auth_user_email_ci_unique ON auth_user (LOWER(email)) WHERE email != '';",
            reverse_sql="DROP INDEX IF EXISTS auth_user_email_ci_unique;",
        ),
    ]