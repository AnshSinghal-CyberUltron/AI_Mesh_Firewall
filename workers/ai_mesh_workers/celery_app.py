"""Celery application — full Django wiring after task modules are migrated."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")

app = Celery("ai_mesh_workers")
app.config_from_object("django.conf:settings", namespace="CELERY")

import django

django.setup()
app.autodiscover_tasks(["ai_mesh_workers.tasks"])

app.conf.beat_schedule = {
    "scan-model-risk-scores": {
        "task": "isolation.scan_model_risk_scores",
        "schedule": 60.0,
    },
}
