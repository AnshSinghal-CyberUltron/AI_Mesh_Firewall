# Implementation Plan: GW32 / GW17b / GW17c

Bottom-up, one commit per layer. Build order (from requirements/design): foundation + R1 -> GW17b -> GW32 -> GW17c -> docs. Detailed steps, gate commands and file paths: `.agents/tasks/gw32/plan.md`.
Conventions: property tests seeded `random.Random`, >=100 iterations, tagged `# Feature: gw32-mcp-transport, Property N: <title>`; negative control per gate; offline only (live gate deferred to the human).

## Tasks

- [ ] 1. Foundation and Requirement 1
  - [ ] 1.1 Capacity allowlist + `runtime/resources_mcp.py` + `runtime/store_keys_mcp.py` + gate test
  - [ ] 1.2 Domain `Principal` MCP fields, `mcp_credential.py`, posture codes, strict `principal_of`, state_control projector, field-name contract test
  - [ ]* 1.3 Property 11 (principal round trip)
  - [ ] 1.4 Identity before method: `resolve_hash`, `mcp_identity.py`, reorder `McpRoute.handle`; update superseded tests
  - [ ]* 1.5 Property 1 (identity first) + negative control
  - [ ] 1.6 Phase gate (pytest, mypy --strict, ruff, lint-imports, AST gates) and commit
- [ ] 2. GW17b authenticated upstreams
  - [ ] 2.1 Control endpoints, migration, tests
  - [ ] 2.2 `dispatch/mcp_credentials.py`, `mcp_transports.py`, broker headers arg
  - [ ]* 2.3 Properties 5, 6, 7, 8
  - [ ] 2.4 Registry `auth_type`/`needs_reauth`, `mcp_cred_gate.py`, `lint/check_credential_flow.py`, L17b-1..4, security design copied to docs/plans
  - [ ] 2.5 Phase gate and commit
- [ ] 3. GW32 transports and OAuth
  - [ ] 3.1 `mcp_paths.py`, `mcp_session_gate.py`, `admit/mcp_sessions.py`
  - [ ]* 3.2 Property 10 (session binding)
  - [ ] 3.3 `mcp_http.py` (POST/GET/DELETE) and `wire/mcp_sse.py`
  - [ ]* 3.4 Property 12 (SSE framing)
  - [ ] 3.5 OAuth 2.1 server (`admit/oauth_*`, `oauth_routes.py`, token identity branch)
  - [ ]* 3.6 Property 9 (PKCE and redirect) + in-process OAuth flow tests
  - [ ] 3.7 Upstream OAuth (`mcp_upstream_oauth.py`, `mcp_upstream_routes.py`)
  - [ ] 3.8 WebSocket (`mcp_ws.py`), `mcp_wiring.py`, `surface_entries.py`, ledger row, real-server fixtures, route-table pinning
  - [ ] 3.9 Phase gate and commit
- [ ] 4. GW17c per-key tool controls
  - [ ] 4.1 `admit/mcp_policy.py`, `admit/mcp_cap.py`, metrics + `SERIES_COUNT`
  - [ ]* 4.2 Properties 2, 3, 4
  - [ ] 4.3 `edge/mcp_policy_gate.py` wired in `_forward`; L17c-1..4; negative controls
  - [ ] 4.4 Phase gate and commit
- [ ] 5. Error table, docs, handoff
  - [ ] 5.1 Error table + drift test; runbook note for decision 11; progress file entry ("code on this tree, offline gates green, live gate not run"); plan doc
  - [ ] 5.2 Human handoff file (DL-1..DL-7, exact commands, D-1..D-7, DD items)
  - [ ] 5.3 Final offline gate: container-run `pytest -q`, `mypy --strict` (gateway_v2, state_control, audit_control), `ruff check`, `lint-imports`, `check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_tenant_scale` (known `state_control/valkey.py:100` finding untouched), `check_sizes`, `check_credential_flow`, `admin_ledger`; record evidence

## Deferred (human, not in this run)
Live Docker/staging gate DL-1..DL-7. Not started, not claimed.
