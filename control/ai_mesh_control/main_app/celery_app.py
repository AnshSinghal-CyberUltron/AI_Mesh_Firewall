import logging
import os

from celery import Celery
from celery.signals import after_setup_logger, worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")

app = Celery("main_app")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_ready.connect
def _resync_gateway_keys_on_boot(**kwargs):
    """On worker startup, immediately reconcile gateway keys into Redis so a
    fresh Redis / recycled container does not leave the data plane down until
    the first periodic beat run."""
    try:
        from core.signals import resync_all_gateway_keys

        resync_all_gateway_keys()
    except Exception:  # noqa: BLE001 - never block worker boot on the reconcile
        logging.getLogger(__name__).exception("gateway-key boot resync failed")


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
