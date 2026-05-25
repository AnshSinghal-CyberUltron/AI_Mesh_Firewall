import logging
import os

from celery import Celery
from celery.signals import after_setup_logger

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")

app = Celery("main_app")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@after_setup_logger.connect
def attach_redis_log_handler(logger, **kwargs):
    """Attach RedisLogPublisher to Celery's logger so worker logs
    appear in the centralized log viewer."""
    try:
        from django.conf import settings

        from ai_mesh_shared.redis_log_handler import RedisLogPublisher

        handler = RedisLogPublisher(
            redis_url=getattr(settings, "REDIS_URL", "redis://redis:6379/0"),
            service_name="Celery",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    except Exception:
        pass
