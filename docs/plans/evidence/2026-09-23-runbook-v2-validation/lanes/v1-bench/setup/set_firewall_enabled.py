"""Set FirewallConfig.firewall_enabled for org v1bench (saved -> synced to Redis by control signals).
usage (inside control): V1B_FW=on|off python /tmp/set_firewall_enabled.py"""
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
django.setup()
from auth.models import Organization
from core.models import FirewallConfig
org = Organization.objects.get(slug="v1bench")
cfg = FirewallConfig.load(organization=org)
cfg.firewall_enabled = os.environ.get("V1B_FW", "on") == "on"
cfg.save()
print(f"[fw] org={org.slug} firewall_enabled={cfg.firewall_enabled}")
