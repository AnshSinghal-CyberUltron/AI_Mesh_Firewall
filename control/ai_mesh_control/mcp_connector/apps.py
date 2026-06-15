from django.apps import AppConfig


class McpConnectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mcp_connector"
    verbose_name = "MCP Connector (OSS Integration)"

    def ready(self) -> None:
        import mcp_connector.signals  # noqa: F401 — register Redis scan-version bump handlers
