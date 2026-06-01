"""
Merge migration — reconciles independent leaves 0024 (D_G5) and 0025 (D_G10).

Both 0024 and 0025 declare dependency ONLY on
`policy/0022_enforcementevent_event_class` to keep G5/G10 land-order
independent at the design level (final_decision_D_G10 A20). Django,
however, requires a single migration-graph leaf per app, so this
no-op merge is the equivalent of `manage.py makemigrations --merge`.

No schema or data changes here.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0024_event_class_query_audit"),
        ("policy", "0025_event_class_hallucination_detected"),
    ]

    operations = []
