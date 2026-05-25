"""Celery application — full Django wiring after task modules are migrated."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")

broker = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")

app = Celery("ai_mesh_workers", broker=broker)
app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

import django

django.setup()
app.autodiscover_tasks(["ai_mesh_workers.tasks"])
