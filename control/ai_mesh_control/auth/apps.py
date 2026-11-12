from django.apps import AppConfig


class AuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "auth"
    label = "auth_api"

    def ready(self):
        # #122: audit every change to is_superuser / is_staff / is_platform_operator.
        from auth import privilege_audit

        privilege_audit.connect()
