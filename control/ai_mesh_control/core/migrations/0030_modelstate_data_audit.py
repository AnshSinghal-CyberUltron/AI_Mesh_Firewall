"""
Data audit for pre-existing invalid ModelState rows (M-20 follow-up).

model_state_views.py now calls ``state.full_clean()`` before every save on
the PATCH /api/models/status/{model}/ and POST /api/models/isolate/ paths.
Rows persisted before that fix could violate the invariants full_clean()
enforces, which would make any unrelated PATCH/isolate on them 400.

What ModelState.full_clean() enforces (and what this migration repairs):
  1. ModelState.clean(): fallback_model must differ from model_name when
     set (self-loop guard)            -> clear fallback_model.
  2. risk_score validators (0.0-100.0) -> clamp into range.
  3. threshold validators (0.0-100.0)  -> clamp into range.
  4. status choices (active/isolated/degraded) -> reset invalid to "active".
  5. action choices (block/reroute/alert)      -> reset invalid to "block".
Max-length, null and PositiveIntegerField (cooldown_seconds) constraints are
already enforced by Postgres and cannot hold invalid data.

Reverse is a no-op: the forward pass only removes invalid data, there is
nothing meaningful to restore.
"""

from django.db import migrations
from django.db.models import F

MODEL_STATUS_VALUES = ["active", "isolated", "degraded"]
MODEL_ACTION_VALUES = ["block", "reroute", "alert"]


def audit_model_state_rows(apps, schema_editor):
    ModelState = apps.get_model("core", "ModelState")

    # 1. Self-loop fallback: clean() only rejects when fallback_model is
    #    truthy, so exclude blank rows (where both could be "").
    ModelState.objects.exclude(fallback_model="").filter(
        fallback_model=F("model_name")
    ).update(fallback_model="")

    # 2. Clamp risk_score into [0.0, 100.0].
    ModelState.objects.filter(risk_score__lt=0.0).update(risk_score=0.0)
    ModelState.objects.filter(risk_score__gt=100.0).update(risk_score=100.0)

    # 3. Clamp threshold into [0.0, 100.0].
    ModelState.objects.filter(threshold__lt=0.0).update(threshold=0.0)
    ModelState.objects.filter(threshold__gt=100.0).update(threshold=100.0)

    # 4. Normalize invalid status values to the model default.
    ModelState.objects.exclude(status__in=MODEL_STATUS_VALUES).update(status="active")

    # 5. Normalize invalid action values to the model default.
    ModelState.objects.exclude(action__in=MODEL_ACTION_VALUES).update(action="block")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_gatewayapikey_encrypted_secret"),
    ]

    operations = [
        migrations.RunPython(audit_model_state_rows, migrations.RunPython.noop),
    ]
