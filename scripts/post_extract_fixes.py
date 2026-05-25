#!/usr/bin/env python3
"""Post-extract fixes: imports, trim non-firewall apps, gateway agent proxy removal."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def replace_shared_imports(root: Path) -> int:
    count = 0
    for py in root.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        text = py.read_text(encoding="utf-8")
        new = text.replace("from shared.", "from ai_mesh_shared.")
        new = new.replace("import shared.", "import ai_mesh_shared.")
        if new != text:
            py.write_text(new, encoding="utf-8")
            count += 1
    return count


def patch_gateway_main() -> None:
    path = ROOT / "gateway/ai_mesh_gateway/main.py"
    text = path.read_text(encoding="utf-8")
    block = re.compile(
        r"\n    # Agent Proxy\n.*?LOG\.info\(\"Agent proxy initialized and routes mounted\"\)\n",
        re.DOTALL,
    )
    if block.search(text):
        text = block.sub(
            "\n    # Agent proxy excluded in AI Mesh Firewall standalone (device fleet not in SKU)\n",
            text,
            count=1,
        )
        path.write_text(text, encoding="utf-8")


def patch_settings() -> None:
    path = ROOT / "control/ai_mesh_control/main_app/settings.py"
    text = path.read_text(encoding="utf-8")
    for app in (
        '    "device.apps.DeviceConfig",\n',
        '    "agent_distribution.apps.AgentDistributionConfig",\n',
    ):
        text = text.replace(app, "")
    path.write_text(text, encoding="utf-8")


def write_urls() -> None:
    content = '''"""
URL configuration — AI Mesh Firewall control plane (no device/agent distribution).
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from core.views import health_view, services_health_view
from core.poc_views import poc_questionnaire_page, poc_questionnaire_submit
from main_app.schema_views import docs_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/gateways/", include("core.gateway_urls")),
    path("api/kill-switches/", include("core.kill_switch_urls")),
    path("api/auth/", include("auth.urls")),
    path("api/health/", health_view),
    path("api/health/services/", services_health_view),
    path("api/policies/", include("policy.urls")),
    path("api/vector-policies/", include("policy.vector_urls")),
    path("api/vector-providers/", include("policy.vector_provider_urls")),
    path("api/policy/", include("policy.evaluation_urls")),
    path("api/security/", include("policy.security_urls")),
    path("api/notifications/", include("policy.notification_urls")),
    path("api/ingestion/", include("core.ingestion_urls")),
    path("api/dashboard/", include("core.dashboard_urls")),
    path("api/firewall/config/", include("core.firewall_config_urls")),
    path("api/firewall/models/", include("core.llm_model_urls")),
    path("api/models/", include("core.model_state_urls")),
    path("api/mcp-connector/", include("mcp_connector.urls")),
    path("api/admin/", include("core.admin_urls")),
    path("ai-mesh-poc", poc_questionnaire_page, name="poc-questionnaire-page"),
    path("api/poc-questionnaire", poc_questionnaire_submit, name="poc-questionnaire-submit"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", docs_view, name="docs"),
]
'''
    (ROOT / "control/ai_mesh_control/main_app/urls.py").write_text(content, encoding="utf-8")


def write_routing() -> None:
    content = '''from channels.routing import URLRouter
from django.urls import path

from ws.routing import websocket_urlpatterns as ws_patterns

websocket_urlpatterns = [
    path("ws/", URLRouter(ws_patterns)),
]
'''
    (ROOT / "control/ai_mesh_control/main_app/routing.py").write_text(content, encoding="utf-8")


def patch_manage() -> None:
    content = '''#!/usr/bin/env python
"""Django CLI — AI Mesh Firewall control plane."""
import os
import sys
from pathlib import Path

def main():
    base = Path(__file__).resolve().parent
    sys.path.insert(0, str(base / "ai_mesh_control"))
    sys.path.insert(0, str(base.parent / "shared"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()
'''
    (ROOT / "control/manage.py").write_text(content, encoding="utf-8")


def main() -> None:
    n = replace_shared_imports(ROOT / "gateway")
    n += replace_shared_imports(ROOT / "workers")
    n += replace_shared_imports(ROOT / "control")
    patch_gateway_main()
    patch_settings()
    write_urls()
    write_routing()
    patch_manage()
    print(f"Updated {n} Python files; patched gateway, settings, urls, routing, manage.py")


if __name__ == "__main__":
    main()
