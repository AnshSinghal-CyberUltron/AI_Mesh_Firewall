"""URL routing for MCP Connector API (/api/mcp-connector/)."""

from django.urls import path

from . import gateway_oauth_flow, views

urlpatterns = [
    # Service health
    path("health/", views.MCPServicesHealthView.as_view(), name="mcp-services-health"),
    # MCP Servers
    path("servers/", views.MCPServerListCreateView.as_view(), name="mcp-server-list-create"),
    path("servers/<uuid:pk>/", views.MCPServerDetailView.as_view(), name="mcp-server-detail"),
    # Per-server tool controls
    path("servers/<uuid:pk>/tools/", views.MCPServerToolListView.as_view(), name="mcp-server-tools"),
    path("servers/<uuid:pk>/tools/<str:tool_name>/", views.MCPToolControlView.as_view(), name="mcp-tool-control"),
    path("scan-controls/", views.MCPScanControlListCreateView.as_view(), name="mcp-scan-control-list"),
    path("scan-controls/<uuid:pk>/", views.MCPScanControlDetailView.as_view(), name="mcp-scan-control-detail"),
    # OAuth 2.1 authorization (Phase C)
    path("servers/<uuid:pk>/oauth/authorize/", views.MCPServerOAuthStartView.as_view(), name="mcp-oauth-authorize"),
    path("oauth/callback/", views.MCPOAuthCallbackView.as_view(), name="mcp-oauth-callback"),
    # Tool discovery
    path("tools/", views.MCPToolListView.as_view(), name="mcp-tool-list"),
    path("tools/call/", views.MCPToolCallView.as_view(), name="mcp-tool-call"),
    # Gateway-internal: enable/disable enforcement helpers
    path("internal/enabled-tools/", views.MCPGatewayEnabledToolsView.as_view(), name="mcp-internal-enabled-tools"),
    path("internal/record-event/", views.MCPGatewayRecordEventView.as_view(), name="mcp-internal-record-event"),
    path("internal/credential/", views.MCPGatewayCredentialView.as_view(), name="mcp-internal-credential"),
    path(
        "internal/credential/refresh-result/",
        views.MCPGatewayCredentialRefreshResultView.as_view(),
        name="mcp-internal-credential-refresh-result",
    ),
    path(
        "internal/oauth-status/",
        gateway_oauth_flow.MCPGatewayOAuthStatusView.as_view(),
        name="mcp-internal-oauth-status",
    ),
    path(
        "internal/oauth-flow/",
        gateway_oauth_flow.MCPGatewayOAuthFlowView.as_view(),
        name="mcp-internal-oauth-flow",
    ),
    path(
        "internal/oauth-flow/consume/",
        gateway_oauth_flow.MCPGatewayOAuthFlowConsumeView.as_view(),
        name="mcp-internal-oauth-flow-consume",
    ),
    path(
        "internal/credential/oauth-tokens/",
        gateway_oauth_flow.MCPGatewayOAuthTokensView.as_view(),
        name="mcp-internal-oauth-tokens",
    ),
    path("internal/needs-reauth/", views.MCPGatewayNeedsReauthView.as_view(), name="mcp-internal-needs-reauth"),
    # Developer diagnostic channel (staff-only) — real cause behind a sanitized
    # client error, looked up by the correlation ref. Never exposed to clients.
    path("diagnostics/<str:ref>/", views.MCPDiagnosticDetailView.as_view(), name="mcp-diagnostic-detail"),
    # Observability events
    path("events/", views.MCPEventListView.as_view(), name="mcp-event-list"),
    path("events/summary/", views.MCPEventSummaryView.as_view(), name="mcp-event-summary"),
    # Org gateway key auto-provisioning
    path("org-gateway-key/", views.OrgGatewayKeyView.as_view(), name="mcp-org-gateway-key"),
]
