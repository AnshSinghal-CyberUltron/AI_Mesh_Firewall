# Implementation Plan: GW32 / GW17b / GW17c (MCP transport, authenticated upstreams, per-key tool controls)

Source of truth: `.kiro/specs/gw32-mcp-transport/design.md` (approved, iteration 3) and `requirements.md` (R1-R22, D-1..D-7, DD-1..DD-18). Do not re-decide the design. Checklist mirror: `.kiro/specs/gw32-mcp-transport/tasks.md`.
Worktree: `/work/suraj/AI_Mesh_Firewall/.worktrees/gw32` (always absolute paths). Branch `gw32-mcp-transport`. Commit locally, one commit per layer, never push.

## Ordering decision
The request names GW32, GW17b, GW17c, but the requirements and design fix the BUILD order: shared foundation + R1 (identity first, a security defect in shipped code) -> GW17b -> GW32 -> GW17c. GW32 transports call `_forward` (shared by every transport) and the credential gate, so GW17b must exist first. GW17c (policy/cap) only needs the foundation, and is placed last so `_forward` is final before the policy gate is added (DD-17). Execution is sequential in one worktree.

## Gate commands (discovered from repo + docs/plans/2026-11-02-gw30-offline-verification-gate.md)
All run from `/work/suraj/AI_Mesh_Firewall/.worktrees/gw32/gateway_v2` (pyproject `pythonpath=["."]`, `testpaths=["tests"]`). Extras: `.[dev,conformance,serve]`; add `websockets`/`httpx` only if the design requires them and they are already optional extras (pin exact versions in pyproject).
Container-run style (no live services, no compose stack):
```
cd /work/suraj/AI_Mesh_Firewall/.worktrees/gw32
docker run --rm -v "$PWD:/repo" -w /repo/gateway_v2 python:3.12-slim bash -c \
  "pip install -q -e '.[dev,conformance,serve]' && pytest -q"
```
If docker is unavailable or has no network, fall back to a local venv (`python3 -m venv /tmp/gw32venv && /tmp/gw32venv/bin/pip install -e 'gateway_v2/.[dev,conformance,serve]'`) and say so in findings. This is a plain pytest container, NOT `docker compose up`.
Individual gates (cwd `gateway_v2/`):
- `pytest -q` (full offline), subset: `pytest -q tests/edge tests/admit tests/dispatch tests/domain tests/runtime tests/state_control tests/gates tests/lint -k "mcp or oauth or lgw17 or lgw32 or gw32"`
- `mypy --strict` (config in pyproject covers gateway_v2; also run `mypy --strict state_control audit_control`)
- `ruff check gateway_v2 state_control audit_control`
- `lint-imports` (all contracts kept)
- `python -m lint.check_capacity_literals gateway_v2`, `python -m lint.check_no_module_mutable gateway_v2`, `python -m lint.check_frozen_dataclasses gateway_v2`, `python -m lint.check_http_outside_edge_resolve gateway_v2`, `python -m lint.check_tenant_scale gateway_v2 state_control` (known pre-existing finding `state_control/valkey.py:100` must remain untouched and unhidden), `python -m lint.check_sizes gateway_v2` (functions <=120 lines, modules <=800), new `python -m lint.check_credential_flow gateway_v2`, `python -m lint.admin_ledger` (confirm the exact CLI args from the file header first).
- Control side (Django): `cd control && python -m pytest ai_mesh_control/mcp_connector -q` (confirm the real test path/settings by reading `control/pyproject.toml` and existing `mcp_connector/tests`; if control tests need Postgres, use the repo's existing sqlite/test settings, else record as not runnable).
Confirm each CLI's exact argument form by reading the gate module header and its `tests/gates/test_check_*.py` before first use.

## Conventions for every task
- Property tests: seeded `random.Random`, >=100 iterations (design uses 200), one file per property, tag in source: `# Feature: gw32-mcp-transport, Property N: <title>` (P1..P12, table in design "Testing"). No new test library.
- Every new capacity number lives only in `runtime/resources_mcp.py` (allowlisted in the capacity gate). Frozen dataclasses, no module-level mutable state, no HTTP objects outside `edge/`, `admit` imports only `runtime`+`domain`.
- Fail closed, never log/trace/audit/metric a credential. Each gate has a negative-control test (R22.5).
- Update superseded tests deliberately (never delete): `tests/edge/test_dev_mcp.py`, `tests/edge/_mcp_fixtures.py` callers (initialize without key), websocket-scope-declined tests, `mcp_upstream_auth_unsupported`-for-bearer tests, `ServerEntry(needs_auth=...)` users. List them in findings/change summary.
- Offline only. Do NOT run docker compose, the staging stack, or any live gate. Never claim a live line.

## Plan

### Phase A - Foundation and Requirement 1 (design steps 1-3)
- [ ] A1. Capacity gate allowlist + `runtime/resources_mcp.py` + `runtime/store_keys_mcp.py`.
      Add the allowlist entry in `gateway_v2/lint/check_capacity_literals.py` with a gate test in `tests/gates/test_check_capacity.py` (include negative control: widening allowlist/literal elsewhere turns red). Create the two runtime modules with every constant named in the design (credential_min_len=8, tool_name_max, bootstrap, token_cache_ttl_s, session bounds, WS bounds...). `{rv2}` hash-tag keys.
      Files: gateway_v2/lint/check_capacity_literals.py, gateway_v2/gateway_v2/runtime/resources_mcp.py, runtime/store_keys_mcp.py, tests/runtime/test_resources_mcp.py, tests/gates/test_check_capacity.py
      Verify: `pytest -q tests/runtime tests/gates` pass; capacity + sizes gates exit 0; `mypy --strict`.
- [ ] A2. Domain + admit identity: `Principal` MCP fields (allow-list, cap), `domain/mcp_credential.py` (UpstreamCredential, RenderedHeaders), append posture codes (`mcp_*` per design error vocabulary) in `domain/posture.py`; strict `principal_of` parsing in `admit/identity.py` (malformed MCP field -> `StoreDataUnavailable`); `state_control` source/projector `_key_body` + `_project_keys` over-bound branches (DD-11, deny-all sentinel `"\u0000deny-all"`); contract test pinning field names across control/projector/`principal_of`. Property 11.
      Files: gateway_v2/gateway_v2/domain/identity.py, domain/mcp_credential.py, domain/posture.py, admit/identity.py, gateway_v2/state_control/{source.py,source_pg.py,projector.py}, tests/domain, tests/admit, tests/state_control (p11 file)
      Verify: `pytest -q tests/domain tests/admit tests/state_control` pass; `mypy --strict gateway_v2 state_control audit_control`; frozen/mutable gates exit 0.
- [ ] A3. Requirement 1: identity before any MCP method. `edge/identity_resolver.py::resolve_hash`, new `edge/mcp_identity.py` (key or `zsmcp_` token -> McpCaller; token branch can be stubbed to "unknown token" until GW32 OAuth lands, fail closed), reorder `edge/mcp.py` `McpRoute.handle`/`_serve` per DD-1 (path, Origin, identity, org match, session, bounded body read, parse). Property 1 + negative control (move `_local` before identity -> red). Update superseded tests.
      Files: gateway_v2/gateway_v2/edge/identity_resolver.py, edge/mcp_identity.py, edge/mcp.py, tests/edge/test_dev_mcp.py, tests/edge/_mcp_fixtures.py, tests/edge/test_mcp_identity_first.py, tests/edge/test_p1_identity_first.py
      Verify: `pytest -q tests/edge` pass; counting fake registry/broker/credential client all 0 on every refusal.
- [ ] A4. Phase A gate: full offline gate set (below). Commit.

### Phase B - GW17b authenticated upstreams (design step 4)
- [ ] B1. Control side: new endpoints in `control/ai_mesh_control/mcp_connector/` (internal-key permission, org scoping, 404 uniformity, throttle scope, CAS idempotence, encrypted fields, credential list never returns secrets, `need_refresh` flag), migration, serializers, URLs, tests. Mirror existing patterns in that package (read views.py/models first).
      Files: control/ai_mesh_control/mcp_connector/{views,urls,serializers,models}.py, migrations/, tests
      Verify: control pytest for mcp_connector passes; migration check `python manage.py makemigrations --check --dry-run` clean (use repo test settings).
- [ ] B2. Dispatch layer: `dispatch/mcp_credentials.py` (control client with injected transport, cache, pure `render`/`leaks`, refresh with cross-worker `SET NX PX` lock DD-3, `needs_reauth`), `dispatch/mcp_transports.py` transport table, `dispatch/mcp_broker.py` headers arg (golden bytes unchanged for `auth none`). Properties 5, 6, 7, 8. `query_param` stays unsupported (DD-16).
      Files: gateway_v2/gateway_v2/dispatch/{mcp_credentials,mcp_transports,mcp_broker}.py, tests/dispatch/test_p5..p8_*.py
      Verify: `pytest -q tests/dispatch` pass; P5 negative control (leaking patch) red.
- [ ] B3. Edge: registry `auth_type`/`needs_reauth` (replace `needs_auth`), `edge/mcp_cred_gate.py` (fetch, refresh, reflection guard -> `output_blocked`, DD-14), wire into `mcp.py` `_forward`. New AST gate `lint/check_credential_flow.py` + test (negative control: credential log line -> red). Tests L17b-1..4 offline forms. Copy GW17b security section to `docs/plans/2026-11-04-gw17b-security-design.md`.
      Files: gateway_v2/gateway_v2/edge/{mcp_registry,mcp_cred_gate,mcp}.py, gateway_v2/lint/check_credential_flow.py, tests/gates/test_check_credential_flow.py, tests/edge/test_l17b_*.py, docs/plans/
      Verify: `pytest -q tests/edge tests/gates` pass; credential-flow gate exits 0.
- [ ] B4. Phase B gate. Commit.

### Phase C - GW32 transports and OAuth (design step 5)
- [ ] C1. Pure edge/admit pieces: `edge/mcp_paths.py` (v1 `/v1/mcp/{slug}` + `/gateway/{org}/mcp/{srv}`, DD-1/D-1 route table pinning), `edge/mcp_session_gate.py` (Origin DD-13, `MCP-Protocol-Version` DD-12), `admit/mcp_sessions.py` (shared Valkey store DD-2, bounded count, touch). Property 10.
      Verify: `pytest -q tests/edge tests/admit`.
- [ ] C2. HTTP: `edge/mcp_http.py` POST/GET/DELETE, `edge/wire/mcp_sse.py` SSE framing (round trip with shipped `SSEDecoder`). Property 12.
      Verify: `pytest -q tests/edge`.
- [ ] C3. OAuth 2.1 server: `admit/oauth_server.py`, `admit/oauth_store.py`, `edge/oauth_routes.py`, token branch in `edge/mcp_identity.py` (DD-10, HMAC-stored tokens, never caches a Principal). Property 9; end-to-end in-process flow (register, authorize, token, MCP call, replay, wrong verifier/redirect, revoked key).
      Verify: `pytest -q tests/admit tests/edge`.
- [ ] C4. Upstream OAuth: `dispatch/mcp_upstream_oauth.py` (discovery, DCR, PKCE, all URLs via `check_remote`), `edge/mcp_upstream_routes.py` (`oauth/start|callback|status`, DD-4), plus control internal endpoints for transient flow state if not done in B1.
      Verify: `pytest -q tests/dispatch tests/edge`; control tests.
- [ ] C5. WebSocket + wiring: `edge/mcp_ws.py` (scope handler, close codes, recheck, bounds), `edge/mcp_wiring.py` (`wrap_mcp_app`, `McpRoute.from_deps`), `surface_entries.py`, console ledger row (`lint/console_inventory.py`/`admin_ledger.py` as the repo requires), real-server fixtures per DD-18 (non-loopback test hostname via injected Resolver; no production loopback permission).
      Verify: `pytest -q tests/edge`; `python -m lint.admin_ledger` and console inventory gate pass.
- [ ] C6. Phase C gate. Commit.

### Phase D - GW17c per-key tool controls (design step 6)
- [ ] D1. `admit/mcp_policy.py` (pure allow-list, `tools/list` filter, DD-5 tool-name validity), `admit/mcp_cap.py` (lease table, store-driven window DD-9, bootstrap DD-8, fail-closed), metrics + `SERIES_COUNT` update. Properties 2, 3, 4.
      Verify: `pytest -q tests/admit`.
- [ ] D2. `edge/mcp_policy_gate.py` (`apply_tool_policy`, DD-6 position + refund via `chat.quota.refund`, DD-7 cap last before `mark_dispatched()`), called from `_forward` only (DD-17, all transports). L17c-1..4 offline forms; negative controls (drop allow-list call -> red).
      Verify: `pytest -q tests/edge tests/admit`.
- [ ] D3. Phase D gate. Commit.

### Phase E - Error table, docs, handoff (design step 7)
- [ ] E1. Error table + drift test (`edge/errors.py`/MCP-only 401 builder), runbook note for owner decision 11 using the exact D-1 sentence, progress file `docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md` entry: GW32, GW17b, GW17c = "code on this tree, offline gates green, live gate not run" with DEFERRED list (do NOT mark done), plan doc under `docs/plans/2026-11-04-gw32-mcp-transport.md`, human handoff `docs/plans/2026-11-04-gw32-human-handoff.md` listing DL-1..DL-7 with exact command per item and D-1..D-7 / DD open decisions. Add a changelog pointer only if the AGENTS.md convention for GW cards requires it (GW cards do not use the four-memory mirror; follow the GW12/GW14d entry style: a short section appended).
      Verify: error-drift test passes.
- [ ] E2. FINAL offline gate (full container-run pytest, mypy --strict x3 trees, ruff, lint-imports, 6 AST gates + credential_flow + sizes + admin_ledger). Record counts in `docs/plans/evidence/2026-11-04-gw32/`. Only pre-existing unrelated failures allowed, listed by name, 0 new regressions.

## Deferred for the human (NOT in this plan)
Live Docker/staging gate DL-1..DL-7 (real MCP servers over HTTP+SSE/WebSocket, real OAuth providers, real Valkey/Postgres failover, multi-replica cap, rolling deploy). Listed only in the handoff file.
