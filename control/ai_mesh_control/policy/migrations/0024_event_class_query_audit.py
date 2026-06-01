"""
D_G5 / Phase-1 Query Audit Telemetry — event_class registration migration.

The `EnforcementEvent.event_class` column is intentionally a free-form
CharField (see `policy/models.py` line 157 and `policy/migrations/
0022_enforcementevent_event_class.py`): the gateway is the source of
truth for event-class values and may extend them independently. The
canonical registry lives in
`gateway/ai_mesh_gateway/telemetry_ops.py:KNOWN_EVENT_CLASSES`.

This migration therefore makes NO schema change. It exists as a
forensic / chain-of-custody marker (D_G5 A8/A9/A20) registering that
the gateway will, from this migration onward, begin emitting the
following operational event_class values:

  * "query_rewritten"
  * "query_blocked"
  * "query_downgraded"

`dependencies` MUST list ONLY `policy/0022_enforcementevent_event_class`
to keep G5 land-order independent from G6's 0023 and G10's 0025
(D_G5 A8, cross-design coupling rule).
"""

from django.db import migrations


# Documented for grep-based forensic audit (D_G5 A9 MANIFEST clause).
G5_EVENT_CLASSES = (
    "query_rewritten",
    "query_blocked",
    "query_downgraded",
)


def _forward(apps, schema_editor):  # noqa: D401 - migration callable
    """No-op forward; serves as a marker that G5 event-classes are live."""
    # Intentionally empty: event_class is free-form. See module docstring.
    return None


def _reverse(apps, schema_editor):  # noqa: D401 - migration callable
    """No-op reverse; the gateway code-side registration is authoritative."""
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0022_enforcementevent_event_class"),
    ]

    operations = [
        migrations.RunPython(_forward, _reverse, elidable=True),
    ]
