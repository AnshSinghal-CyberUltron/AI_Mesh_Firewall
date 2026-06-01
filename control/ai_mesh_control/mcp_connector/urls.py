"""URL routing for MCP Connector API (/api/mcp-connector/)."""

from django.urls import path

from . import views

urlpatterns = [
    # Service health
    path("health/", views.MCPServicesHealthView.as_view(), name="mcp-services-health"),
    # MCP Servers
    path("servers/", views.MCPServerListCreateView.as_view(), name="mcp-server-list-create"),
    path("servers/<uuid:pk>/", views.MCPServerDetailView.as_view(), name="mcp-server-detail"),
    # Per-server tool controls
    path("servers/<uuid:pk>/tools/", views.MCPServerToolListView.as_view(), name="mcp-server-tools"),
    path("servers/<uuid:pk>/tools/<str:tool_name>/", views.MCPToolControlView.as_view(), name="mcp-tool-control"),
    # OAuth 2.1 authorization (Phase C)
    path("servers/<uuid:pk>/oauth/authorize/", views.MCPServerOAuthStartView.as_view(), name="mcp-oauth-authorize"),
    path("oauth/callback/", views.MCPOAuthCallbackView.as_view(), name="mcp-oauth-callback"),
    # Tool discovery
    path("tools/", views.MCPToolListView.as_view(), name="mcp-tool-list"),
    path("tools/call/", views.MCPToolCallView.as_view(), name="mcp-tool-call"),
    # Gateway-internal: enable/disable enforcement helpers
    path("internal/enabled-tools/", views.MCPGatewayEnabledToolsView.as_view(), name="mcp-internal-enabled-tools"),
    path("internal/record-event/", views.MCPGatewayRecordEventView.as_view(), name="mcp-internal-record-event"),
    path("internal/needs-reauth/", views.MCPGatewayNeedsReauthView.as_view(), name="mcp-internal-needs-reauth"),
    # Observability events
    path("events/", views.MCPEventListView.as_view(), name="mcp-event-list"),
    path("events/summary/", views.MCPEventSummaryView.as_view(), name="mcp-event-summary"),
    # Org gateway key auto-provisioning
    path("org-gateway-key/", views.OrgGatewayKeyView.as_view(), name="mcp-org-gateway-key"),
]
