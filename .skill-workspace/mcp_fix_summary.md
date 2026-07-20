# MCP Hardening — Fix Summary & Verification Sign-off

Branch: `fix/mcp-hardening` (LOCAL ONLY). Scope: mcp #1–#4. Verified live on the local docker control container.
All edits in `control/ai_mesh_control/mcp_connector/views.py` (+ that file only). No DB migration required.

## Findings → fixes
- mcp #1 (disabled-tool bypass via X-Server-Slug): MCPToolCallView now loads ALL of the org's registrations for
  the tool; blocks if disabled on ANY server (slug-switch can't evade); requires a registered+enabled tool for the
  resolved server, else 403 `tool_not_registered`.
- mcp #2 (record-event forgery + stored XSS): new `IsGatewayInternalOnly` permission on MCPGatewayRecordEventView
  (rejects user JWT — gateway shared-secret only); org from trusted `_gateway_request_org`; central
  `_record_event` sanitises+caps free-text (strips control chars + `<`/`>`, caps tool/server 255, reason 500) —
  protects every recording path.
- mcp #3 (REFRAMED per owner → strict org isolation, no SSRF): `_request_org` now derives org ONLY from the
  authenticated principal's profile or trusted gateway header — drops `get_request_organization` (which honored a
  client `organization_id` for superusers = cross-org hole). Scan-control create rejects a server FK from another org.
  Effect: all MCP views (servers/tools/calls/events/scan-controls) are strictly own-org.
- mcp #4 (write/dangerous classifier — FLAG ONLY): `_classify_tool_write_risk()` (MCP annotation hints else name
  heuristic) → read|write|destructive. Allow path attaches `metadata.write_risk` + `is_write_tool` and logs a
  warning for write/destructive tools. Does NOT block (per owner).

## Verification sign-off (Phase 6E) — live, in-container HTTP + shell
mcp #3: server create with `?organization_id=<other>` lands in OWN org (override ignored); org2 received no leak;
        cross-org scan-control server FK → 400.                                              [PASS]
mcp #2: record-event with user JWT → 403; with gateway secret → 201; stored `<script>…` → angle brackets stripped,
        capped to 255; reason sanitized.                                                     [PASS]
mcp #1: disabled tool called via a different X-Server-Slug → 403 tool_disabled; unknown tool → 403
        tool_not_registered.                                                                 [PASS]
mcp #4: delete_record/merge_pull_request → destructive; save_comment/create_issue → write; get_me/search_issues →
        read; destructiveHint annotation overrides name.                                     [PASS]
Totals: 18/18 checks PASS. `manage.py check` clean (pre-existing W342 only). views.py compiles. Services healthy.
Status: VERIFIED.

## Caveats / follow-ups
- Verified via `docker cp` into the RUNNING control container (ephemeral) + restart; authoritative change is on the
  host branch. Deploy to prod requires REBUILDING the control image. No migration needed.
- mcp #4 integration flag (metadata.write_risk on the allow path) is wired + the classifier unit-verified; the
  end-to-end allow path needs a live upstream MCP server to exercise fully (couldn't forward to a real server
  locally) — verified by code + classifier tests.
- mcp #1 "require registered+enabled tool" means a tool with NO registration row is now denied. If any legitimate
  flow calls tools before they are discovered/registered, ensure discovery runs first (expected: discovery
  populates MCPToolRegistration). Monitor `tool_not_registered` blocks after deploy.
- SSRF (original mcp #3) intentionally NOT implemented per owner ("no need for that level; just strict org
  isolation"). If desired later, add a private/metadata-IP deny-list on server-create + gateway fetch.
