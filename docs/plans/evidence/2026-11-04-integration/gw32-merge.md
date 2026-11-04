# GW32 (incl. GW17b/GW17c) merge into integration-gw27-35
- Merge commit: 1c3e749b (git merge --no-ff gw32-mcp-transport onto GW29 note 94332129)
- Fix commit: 8b10093b (test-only, separate from the merge)
- Handoff: no HANDOFF.md at gw32 root; read `docs/plans/2026-11-04-gw32-human-handoff.md` (DL-1..DL-7 live gate, D-10 deploy order for `AMF_PROJECT_MCP_KEY_POLICY`).

## Conflicts + resolutions (6 files)
- edge/mcp.py: GW32 resolves the caller once (`resolve_caller` -> `ctx.caller`); `_tenant` now uses `ctx.caller.identity` (GW29's `identity_of` re-resolve dropped) and then calls GW29's `_incident_org_gate`. `_send` keeps GW29 `incident_is_refusal` retry headers AND GW32 `mcp-session-id` + SSE framing. Imports: `TenantIdentity` + `incident_is_refusal` (unused `hashed_bearer_key`/`identity_of` dropped).
- edge/dev_profile.py: GW32's `_attach_mcp` helper (passes `identity=deps["identity"]`, wires `attach_mcp_policy`) kept; GW29 imports (`PassThroughIncidentGate`, `DevNoControlSource`) kept alongside `McpDeps`; `deps` update unchanged in content.
- edge/publisher.py: union. Slots `_audit_suppressed`, `_incident`, `_mcp_policy`; init keeps both; `attach_mcp_policy` + `_mcp_policy_families` added next to GW29 `observe_audit_suppressed`.
- lint/check_capacity_literals.py: `ALLOWED_RELS` = resources.py + resources_org.py (GW27) + resources_mcp.py (GW32); GW32's generalized matching and message.
- state_control/projector.py: imports union (`IncidentScopePolicy`... + `SourceKey`, `SourceSwitch`); `desired_state(view, incident_scopes=None, *, mcp_key_policy=False)`; `round_once` passes both.
- docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md: GW17b/GW17c rows from GW32, GW27 row from integration.

## Fix commit 8b10093b (tests that encoded a single-branch view)
- tests/gates/test_gw27_capacity_gate_allowlist.py: allowlist test now expects the three declared modules.
- tests/edge/test_lgw29_misc_surfaces.py `test_every_post_route_is_classified`: GW32 `/oauth/{register,authorize,token}` and the `/gateway/` MCP prefix (gated in `McpRoute._tenant`) are classified.

## Gates (from gateway_v2/, venv /tmp/intv)
| Tool | Result |
|---|---|
| pytest -n 24 (AMF_EDGE_DOCKER=0) | 4084 passed, 108 skipped, 1 failed (test_check_tenant_scale::test_the_shipped_data_plane_is_clean = known pinecone_transport.py:140) |
| ruff check gateway_v2 state_control lint tests | all passed |
| mypy --strict | 8 errors, all import-not-found for optional vector SDKs (baseline); 0 other (307 files) |
| lint-imports | 3 kept, 0 broken |
| check_sizes / http_outside_edge_resolve / no_module_mutable / capacity_literals / frozen_dataclasses / dispatch_provenance x gateway_v2 + state_control | all exit 0 |
| check_secret_headers --repo .. , check_console_inventory, check_credential_flow | exit 0 |
| check_tenant_scale | only known: pinecone_transport.py:140 |
Not run: control Django tests (need throwaway Postgres+Redis) and the GW32 live gate DL-1..DL-7. Production profile builds no MCP deps (GW36).
