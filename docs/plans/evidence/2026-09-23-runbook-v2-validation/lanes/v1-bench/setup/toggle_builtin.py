"""Toggle the v1bench org's built-in detection packs (enabled flag) and recompile the org bundle.
usage (inside control): V1B_BUILTIN=on|off python /tmp/toggle_builtin.py"""
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
django.setup()
from auth.models import Organization
from policy.models import Policy
from policy.policy_package.seed import compile_organization_policies
org = Organization.objects.get(slug="v1bench")
on = os.environ.get("V1B_BUILTIN", "off") == "on"
n = Policy.objects.filter(organization=org, code__startswith="BUILTIN_").update(enabled=on)
compile_organization_policies(org)
pol = Policy.objects.filter(organization=org)
print(f"[toggle] builtin enabled={on} ({n} policies); enabled={pol.filter(enabled=True).count()} pipeline_enabled={pol.filter(enabled=True, policy_domain='pipeline').count()}")
