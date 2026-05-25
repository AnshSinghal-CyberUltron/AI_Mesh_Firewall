from django.apps import AppConfig


class McpConnectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mcp_connector"
    verbose_name = "MCP Connector (OSS Integration)"
