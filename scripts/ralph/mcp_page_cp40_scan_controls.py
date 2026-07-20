"""CP40 (run inside control via manage.py shell): Scan Controls + Scan Control Matrix
— every control persists AND has a REAL EFFECT on the effective-control resolution
(the authoritative resolve_effective_controls the tool-call path uses). Proves
CRUD (create org + server scope rows, edit, delete) and precedence (server > org).
Rows are tagged priority=777 for clean teardown (the model has no metadata field).
"""
import json
from auth.models import Organization
from mcp_connector.models import MCPScanControl, MCPServerRegistration
from mcp_connector.scan_controls import resolve_effective_controls, serialize_control

MARK = 777
org = Organization.objects.filter(slug="zeroshield").first()
out = {"steps": {}}

# teardown any leftovers from a prior run
MCPScanControl.objects.filter(organization=org, priority=MARK).delete()
MCPServerRegistration.objects.filter(organization=org, name__startswith="cp40-").delete()

srv = MCPServerRegistration.objects.create(
    organization=org, name="cp40-srv", server_slug="cp40-srv",
    transport="stdio", command="npx", auth_type="none")
other = MCPServerRegistration.objects.create(
    organization=org, name="cp40-other", server_slug="cp40-other",
    transport="stdio", command="npx", auth_type="none")

def eff(server):
    rows = [serialize_control(c) for c in MCPScanControl.objects.filter(organization=org)]
    e = resolve_effective_controls(rows, server_id=str(server.id), tool_name="echo")
    return (e.get("tier1_input") or {}).get("action")

out["steps"]["baseline"] = {"srv": eff(srv)}

# CREATE org-scope tier1 input row action=redact
org_row = MCPScanControl.objects.create(
    organization=org, server=None, tool_name="", tier="tier1",
    direction="input", scope_type="org", action="redact", enabled=True, priority=MARK)
out["steps"]["after_org_redact"] = {"srv": eff(srv), "other": eff(other)}

# CREATE server-scope tier1 input row action=block on srv (overrides org for srv)
srv_row = MCPScanControl.objects.create(
    organization=org, server=srv, tool_name="", tier="tier1",
    direction="input", scope_type="server", action="block", enabled=True, priority=MARK)
out["steps"]["after_server_block"] = {"srv": eff(srv), "other": eff(other)}

# EDIT server row action → tag
srv_row.action = "tag"; srv_row.save()
out["steps"]["after_server_edit_tag"] = {"srv": eff(srv)}

# DELETE server row → srv falls back to org (redact)
srv_row.delete()
out["steps"]["after_server_delete"] = {"srv": eff(srv)}

# DELETE org row → default
org_row.delete()
out["steps"]["after_org_delete"] = {"srv": eff(srv)}

srv.delete(); other.delete()
MCPScanControl.objects.filter(organization=org, priority=MARK).delete()

s = out["steps"]
checks = {
    "org_row_applies_both": s["after_org_redact"]["srv"] == "redact" and s["after_org_redact"]["other"] == "redact",
    "server_row_overrides_org": s["after_server_block"]["srv"] == "block" and s["after_server_block"]["other"] == "redact",
    "edit_reflected": s["after_server_edit_tag"]["srv"] == "tag",
    "delete_falls_back_to_org": s["after_server_delete"]["srv"] == "redact",
    "org_delete_back_to_default": s["after_org_delete"]["srv"] != "redact",
}
out["checks"] = checks
out["cp40Pass"] = all(checks.values())
print("CP40_RESULT=" + json.dumps(out))
