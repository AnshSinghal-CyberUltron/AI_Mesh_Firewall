"""
D_G10 / Semantic Hallucination Grounding — event_class registration migration.

Mirrors `0024_event_class_query_audit.py` (D_G5). The
`EnforcementEvent.event_class` column is free-form (see
`policy/migrations/0022_enforcementevent_event_class.py` docstring) and
the gateway-side canonical registry lives in
`gateway/ai_mesh_gateway/telemetry_ops.py:KNOWN_EVENT_CLASSES`.

This migration is a no-op marker registering that the gateway begins
emitting:

  * "hallucination_detected"

`dependencies` MUST list ONLY `policy/0022_enforcementevent_event_class`
to keep G10 land-order independent from G5's 0024 and G6's 0023
(D_G10 A20, cross-design coupling rule).
"""

from django.db import migrations


# Documented for grep-based forensic audit.
G10_EVENT_CLASSES = ("hallucination_detected",)


def _forward(apps, schema_editor):  # noqa: D401 - migration callable
    return None


def _reverse(apps, schema_editor):  # noqa: D401 - migration callable
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0022_enforcementevent_event_class"),
    ]

    operations = [
        migrations.RunPython(_forward, _reverse, elidable=True),
    ]
