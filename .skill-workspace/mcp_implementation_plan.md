# MCP Hardening — Implementation Plan (focused, branch `fix/mcp-hardening`, LOCAL ONLY)

All in control/ai_mesh_control/mcp_connector/ unless noted. Verify on local docker stack.

## mcp #3 — strict org isolation (user: "no SSRF; just strict org isolation everywhere")
- views.py `_request_org`: derive org ONLY from authenticated principal's profile (JWT) or trusted gateway
  header — drop `get_request_organization` (which honors client organization_id for superusers = cross-org hole).
  Remove now-unused `from auth.utils import get_request_organization` import.
- views.py MCPScanControlListCreateView.post: reject a `server` FK whose organization != request org (400).
- Effect: all MCP views (server list/create/detail, tool list/call, events, scan-controls) become strictly own-org;
  superuser organization_id act-as no longer crosses tenants on the MCP surface.

## mcp #2 — record-event internal-only + sanitize/cap
- views.py: add `IsGatewayInternalOnly` permission (only `_is_gateway_internal_request`).
- MCPGatewayRecordEventView: permission_classes=[IsGatewayInternalOnly]; org=_gateway_request_org(request).
- _record_event (central choke point): sanitize+cap free-text (tool_name/server_name/server_slug<=255, reason<=500):
  strip control chars + angle brackets (kills stored <script>), collapse, cap. Protects every recording path.

## mcp #1 — disabled-tool bypass via X-Server-Slug
- MCPToolCallView enable/disable block: load ALL org regs for tool_name; (a) block if disabled on ANY server
  (slug-switch evasion guard); (b) require a registered+enabled reg for the RESOLVED server, else 403
  "tool_not_registered". Keep resolved_server wiring intact.

## mcp #4 — write/dangerous classifier (FLAG ONLY, no block)
- views.py `_classify_tool_write_risk(tool_name, description)` -> 'read'|'write'|'destructive' (MCP annotation
  hints if present, else name keyword heuristic).
- MCPToolCallView allow path: attach metadata.write_risk + log a flag when write/destructive; decision unchanged.

## Verify (local docker stack, in-container python)
- mcp #3: JWT user with ?organization_id=<other> on POST /servers/ lands in OWN org (not other); cross-org server
  FK on scan-control -> 400; list/detail only own-org.
- mcp #2: record-event with user JWT -> 403; with gateway secret headers -> 201; tool_name '<script>..' stored
  stripped/capped.
- mcp #1: disable tool on server A, call with X-Server-Slug=B -> blocked; unknown tool -> 403.
- mcp #4: call a 'delete_*'/'save_*' tool -> event metadata.write_risk set, decision still allow.
- Regression: manage.py check; existing mcp tests if any.

## Rollback: local branch; `git checkout` reverts. No migrations (all code-level; MCPToolRegistration already has fields).
