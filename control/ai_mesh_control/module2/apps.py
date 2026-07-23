from django.apps import AppConfig


class Module2Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "module2"
    verbose_name = "ZeroShield Module 2 — Control Plane"

    def ready(self) -> None:
        import module2.signals  # noqa: F401
