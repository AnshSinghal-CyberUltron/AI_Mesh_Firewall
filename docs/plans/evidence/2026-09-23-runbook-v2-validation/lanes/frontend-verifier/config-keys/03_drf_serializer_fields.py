#!/usr/bin/env python3
"""Confirm Task 1(a)(b) with Django/DRF introspection, fully isolated from live infra:
- DATABASE_URL unset -> sqlite at a scratch path (never opened: no queries are made)
- REDIS_URL -> redis://127.0.0.1:1/0 (unreachable port; nothing is sent)
- sys.argv contains 'test' -> AppConfig.ready() skips background jobs/auto-compile
Only model _meta and serializer .fields are inspected (no .data, no .save, no queries).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv = ["introspect", "test"]
for k in ("DATABASE_URL", "DATABASE_REPLICA_URL", "DATABASE_ANALYTICS_URL", "CHANNEL_LAYERS_REDIS_URL"):
    os.environ.pop(k, None)
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
os.environ["DJANGO_DB_PATH"] = os.path.join(HERE, "introspect-unused.sqlite3")
os.environ["DEBUG"] = "True"
os.environ["DJANGO_SECRET_KEY"] = "scratch-introspection-only-" + "x" * 40
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
sys.path.insert(0, "/home/contact_cyberultron_com/AI_Mesh_Firewall/control/ai_mesh_control")
sys.path.insert(0, "/home/contact_cyberultron_com/AI_Mesh_Firewall/shared")

import django  # noqa: E402

django.setup()
from django.db import connection  # noqa: E402

from core.models import FirewallConfig  # noqa: E402
from core.firewall_config_serializer import FirewallConfigSerializer  # noqa: E402

concrete = [f for f in FirewallConfig._meta.get_fields() if getattr(f, "concrete", False)]
print(f"(a) FirewallConfig concrete fields via _meta: {len(concrete)}")
print("    " + ", ".join(f.name for f in concrete))
ser = FirewallConfigSerializer()
fields = ser.fields
print(f"(b) FirewallConfigSerializer().fields: {len(fields)}")
ro = [n for n, f in fields.items() if f.read_only]
wr = [n for n, f in fields.items() if not f.read_only]
print(f"    read_only ({len(ro)}): {ro}")
print(f"    writable ({len(wr)}): {len(wr)} fields")
org = fields.get("organization")
print(f"    'organization' field class={type(org).__name__} read_only={getattr(org, 'read_only', None)} required={getattr(org, 'required', None)}")
print(f"    names: {list(fields.keys())}")
print(f"DB connection opened during introspection? {connection.connection is not None}")
