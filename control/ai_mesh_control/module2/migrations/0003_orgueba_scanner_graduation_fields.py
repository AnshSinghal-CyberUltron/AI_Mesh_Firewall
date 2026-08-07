"""Scanner-specific graduation fields were never used in the Module 2 UI.

Kept as an empty migration so the graph stays: 0002 → 0003 → 0004.
Prompt/request threshold is org ``behavior_profile_prompt_target`` only.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("module2", "0002_ueba_v2_models"),
    ]

    operations = []
