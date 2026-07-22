"""
Scalar API documentation view.

/docs/ -- Consolidated ZeroShield API documentation with Backend + Gateway spec selector.
"""

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from drf_spectacular.views import SpectacularAPIView
from rest_framework.permissions import IsAuthenticated

from core.admin_views import IsAdminOrSuperuser


class ProtectedSpectacularAPIView(SpectacularAPIView):
    """OpenAPI schema — admin/staff/platform_admin only (CDL finding #12)."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperuser]


def docs_view(request):
    """
    Serve the consolidated ZeroShield Scalar API documentation page.

    Renders an interactive API reference with a spec selector dropdown
    to switch between the Backend (Control Plane) and Gateway (Data Plane)
    OpenAPI specifications.

    Spec switching uses a query-parameter page reload (`?spec=gateway`)
    because the Scalar CDN library does not support dynamic re-initialization
    after the initial page load.
    """
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    if not IsAdminOrSuperuser().has_permission(request, None):
        return HttpResponseForbidden("Admin access required.")

    gateway_url = (getattr(settings, "GATEWAY_PUBLIC_URL", None) or "").strip().rstrip("/")
    if not gateway_url:
        gateway_url = (request.build_absolute_uri("/") or "").rstrip("/")

    spec_param = request.GET.get("spec", "backend")
    if spec_param == "gateway":
        active_spec_url = f"{gateway_url}/openapi.json"
        backend_selected = ""
        gateway_selected = "selected"
    else:
        active_spec_url = "/api/schema/"
        backend_selected = "selected"
        gateway_selected = ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>ZeroShield API Documentation</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; font-family: system-ui, -apple-system, sans-serif; background: #0f0f23; }}
        .docs-header {{
            position: sticky; top: 0; z-index: 1000;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #fff; padding: 14px 28px;
            display: flex; align-items: center; gap: 20px;
            border-bottom: 1px solid rgba(233, 69, 96, 0.3);
            box-shadow: 0 2px 12px rgba(0, 0, 0, 0.3);
        }}
        .docs-header .logo {{
            font-size: 18px; font-weight: 700; margin: 0;
            letter-spacing: -0.5px;
        }}
        .docs-header .logo span {{
            color: #e94560;
        }}
        .docs-header .separator {{
            width: 1px; height: 24px; background: rgba(255, 255, 255, 0.2);
        }}
        .docs-header .spec-label {{
            font-size: 13px; color: rgba(255, 255, 255, 0.6);
            text-transform: uppercase; letter-spacing: 1px;
        }}
        .docs-header select {{
            padding: 8px 14px; border-radius: 8px;
            border: 1px solid rgba(233, 69, 96, 0.4);
            background: rgba(22, 33, 62, 0.8); color: #fff;
            font-size: 14px; cursor: pointer;
            transition: border-color 0.2s;
        }}
        .docs-header select:hover {{
            border-color: #e94560;
        }}
        .docs-header select:focus {{
            outline: none; border-color: #e94560;
            box-shadow: 0 0 0 2px rgba(233, 69, 96, 0.2);
        }}
        .docs-header .version {{
            margin-left: auto;
            font-size: 12px; color: rgba(255, 255, 255, 0.4);
        }}
        #scalar-container {{
            min-height: calc(100vh - 56px);
        }}
    </style>
</head>
<body>
    <div class="docs-header">
        <h1 class="logo">Zero<span>Shield</span> API</h1>
        <div class="separator"></div>
        <span class="spec-label">Spec</span>
        <select id="spec-select" onchange="switchSpec(this.value)">
            <option value="backend" {backend_selected}>Backend (Control Plane)</option>
            <option value="gateway" {gateway_selected}>Gateway (Data Plane)</option>
        </select>
        <span class="version">v1.0.0</span>
    </div>
    <div id="scalar-container">
        <script
            id="api-reference"
            data-url="{active_spec_url}"
            data-configuration='{{"theme": "kepler", "hideDownloadButton": false, "defaultHttpClient": {{"targetKey": "python", "clientKey": "requests"}}}}'
        ></script>
        <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.25.55"></script>
    </div>
    <script>
        function switchSpec(spec) {{
            window.location.href = '/docs/?spec=' + encodeURIComponent(spec);
        }}
    </script>
</body>
</html>"""
    return HttpResponse(html, content_type="text/html")
