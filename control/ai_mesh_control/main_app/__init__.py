from .celery_app import app as celery_app

app = celery_app  # For: celery -A main_app worker
__all__ = ("app", "celery_app")
