"""URL routing for firewall configuration API."""

from django.urls import path

from core.firewall_config_views import FirewallConfigView

urlpatterns = [
    path("", FirewallConfigView.as_view(), name="firewall-config"),
]
