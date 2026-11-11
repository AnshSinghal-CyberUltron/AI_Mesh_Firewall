"""F5 repro: a control->gateway hop 401 is classified as the MCP server rejecting auth.

Run from control/ with DATABASE_URL/REDIS_URL/DJANGO_SECRET_KEY exported (no query is made):
    /home/claude/work/venv-ctl312/bin/python <evidence>/repro_f5.py
"""
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
sys.path.insert(0, "ai_mesh_control")
django.setup()
from mcp_connector import views  # noqa: E402

# What _discover_tools_via_gateway feeds the classifier when the gateway answers 401 (views.py:823).
raw = 'gateway HTTP 401: {"detail":"Missing or malformed Authorization header"}'
code, summary = views._classify_sync_error(raw.lower())
print("code:", code)
print("summary:", summary)
hints = ("interactive authentication", "requires re-authentication", "re-authentication",
         "re-authenticate", "re-authorize", "byok / oauth", "needs_reauth", "upstream returned 401",
         "401;", " 401", "invalid_token", "invalid token", "unauthorized")
print("needs_reauth heuristic (views.py:922) fires on:", [h for h in hints if h in summary.lower()])
