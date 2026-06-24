"""Celery application — full Django wiring after task modules are migrated."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")

app = Celery("ai_mesh_workers")
app.config_from_object("django.conf:settings", namespace="CELERY")

import django

django.setup()

# Workers-local tasks plus all Django INSTALLED_APPS tasks (module2, core, policy, …).
app.autodiscover_tasks(["ai_mesh_workers.tasks"])
app.autodiscover_tasks()

# Merge Django CELERY_BEAT_SCHEDULE with workers-only entries — never replace the full schedule.
from django.conf import settings

_workers_beat = {
    "scan-model-risk-scores": {
        "task": "isolation.scan_model_risk_scores",
        "schedule": 60.0,
    },
}
app.conf.beat_schedule = {
    **getattr(settings, "CELERY_BEAT_SCHEDULE", {}),
    **_workers_beat,
}
