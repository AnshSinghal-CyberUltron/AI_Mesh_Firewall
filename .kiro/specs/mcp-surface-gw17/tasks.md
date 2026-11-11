# Implementation Plan: GW17 MCP surface on the shared plan and resolver

## Overview

These steps turn the approved design into code. `POST /v1/mcp/{server_slug}` is served on the shipped v2 lifecycle: identity, the one plan read, admission, budget, chat's detectors and resolver. It forwards only through the existing broker. RAG and vector stay HTTP 501.

Work is bottom-up along the import-linter layer order: `domain` → `runtime` → `dispatch` → `edge`. Each wave passes its own gates before the next one builds on it. Isolation code is ported, not rewritten (runbook GW17 agent notes). The broker, the sandbox agent, control, v1, and the frontend are not changed.

Repo `/home/suraj/Documents/zeroshield/AI_Mesh_Firewall`, branch `suraj-revamp`. Python commands run from `gateway_v2/` with `.venv/bin/python`, `.venv/bin/mypy`, and `.venv/bin/ruff`.

Do not commit unless the operator asks. Do not push. Do not edit `gateway/ai_mesh_gateway/`, `services/mcp-broker/`, `control/`, or `frontend/`. Do not write the Redis key `{rv2}:dev:firewall-plan`. Do not register an MCP server in control without the operator's say-so. Do not mark GW17 done in the runbook until the operator has reviewed the local proof.

**Stop rule (runbook GW17).** Stop and report if any isolation control regresses against v1. That covers the SSRF refusals, `allowed_hosts`, the stdio allowlist, the body caps, broker-only egress, and per-org sandboxing.

### Conventions enforced in every coding task

- Value types are frozen slotted dataclasses. Module-level tables use `MappingProxyType` or `frozenset`. No module-level mutable.
- No capacity literal outside `runtime/resources.py`. Every MCP bound, timeout, retry count, and TTL is read from `ResourceContract`.
- `dispatch/` modules import only `runtime` and `domain`. `egress` does not import `detect`. HTTP framework objects stay in `edge/`.
- Fail closed everywhere. A refusal before the broker call makes zero broker calls. `fail_open_total` stays 0.
- Modules stay at or under 800 lines and functions at or under 120. `edge/routes.py` is 798 lines today, so route entries go into a new helper module.
- `mypy --strict` and ruff (line length 100) are clean on every product file the wave touches.
- Property tests use a seeded `random.Random` for at least 10,000 iterations, with no hypothesis. Tag each one `# Feature: mcp-surface-gw17, Property N: <text>` and `# Validates: Requirements X.Y`.
- Network I/O is behind an injected client factory or transport, so every local test runs with fakes and no socket.

## Tasks

- [x] 1. Channels and bounds (domain, runtime)
  - [x] 1.1 Add the MCP text channels **[product-code]**
    - Modify `gateway_v2/gateway_v2/domain/channels.py`
    - Add `mcp_arguments` (`mcp:params.arguments`), `mcp_completion` (`mcp:params.argument.value`), `mcp_result` (`mcp:result`), and `mcp_error` (`mcp:error`), all `scanned=True`
    - Modify `gateway_v2/gateway_v2/edge/wire/openai_misc.py` so its misc fold check also excludes the `mcp:` path prefix. The chat check is unaffected because it only looks at `messages[]`
    - Update the channel and fold tests that pin the channel set: `tests/domain/test_gw16_channels.py`, `tests/domain/test_lgw04.py`, `tests/edge/test_lgw15_fold.py`, `tests/edge/test_gw16_fold.py`, and any detector-framework test that counts channels. Read each assertion first. Only the set changes; no check is loosened
    - _Design: Component 2_
    - _Requirements: 4.1, 4.2, 5.1, 5.2, 5.3_

  - [x] 1.2 Add the MCP bounds to the resource contract **[product-code]**
    - Done as one frozen `McpBounds` value from `ResourceContract.mcp_bounds()`, so `resources.py` stays under 800 lines; every number is still only in `resources.py`
    - Modify `gateway_v2/gateway_v2/runtime/resources.py`
    - Add the named constants and contract methods from design Component 1: body, argument, and result byte bounds; JSON depth; argument and result node counts; result block count; broker init and method timeouts; broker retry count, backoff base, and backoff cap; registry TTL, fetch timeout, and poll interval
    - Each method fails closed with `CapacityUnavailable` on a non-positive value, matching the shipped methods
    - Add `mcp_request_body_bytes()` returning `min(mcp_max_body_bytes(), max_request_bytes())`
    - _Design: Component 1_
    - _Requirements: 3.1, 6.7, 7.2, 9.1, 9.2, 9.3_

  - [x] 1.3 Check the wave **[product-code]**
    - `mypy --strict` and ruff clean on both modules
    - The capacity-literal and module-mutable gates exit 0
    - The channel and fold test files from 1.1 pass, and `tests/runtime` passes
    - _Requirements: 11.5_

- [x] 2. Gateway SSRF guard (dispatch)
  - [x] 2.1 Port the outbound URL check **[product-code]**
    - Create `gateway_v2/gateway_v2/dispatch/url_guard.py`
    - Port the rules of v1 `gateway/ai_mesh_gateway/_url_guard.py:59-196` without rewriting their logic:
      - schemes `http`, `https`, `ws`, `wss` only
      - no userinfo and no `@` in the authority
      - a literal IP is checked directly; a hostname is resolved through an injected async resolver and every resolved address is checked
      - refuse loopback, private, link-local (all of `169.254/16`), reserved, multicast, unspecified, IPv4-mapped IPv6, and the metadata addresses `169.254.169.254` and `fd00:ec2::254`
      - a DNS failure is a refusal
    - `allow_hosts` is an exact-host `frozenset`. An allowlisted host skips the private-range check but never the metadata check. An empty set allows nothing (stricter than v1, recorded in the design)
    - Add `check_stdio(command, allowed)`: refuse a path-qualified command or one outside `allowed`
    - Return `None` or a short reason string. Never raise to the caller, and never include a resolved IP or a hostname in anything a client sees
    - _Design: Component 5_
    - _Requirements: 6.4, 6.5_

  - [x] 2.2 SSRF matrix tests **[test-file]**
    - Create `gateway_v2/tests/dispatch/test_url_guard.py`
    - Cases, each with a fake resolver:
      - refused: `127.0.0.1`, `localhost` resolving to loopback, `::1`, `10.0.0.5`, `192.168.1.1`, `169.254.169.254`, `169.254.1.1`, `fd00:ec2::254`, `::ffff:127.0.0.1`, `0.0.0.0`
      - refused: a public name that resolves to any blocked address (the rebinding shape the guard can see)
      - refused: DNS failure, `ftp://`, userinfo `http://a@b`
      - allowed: a public address
      - allowlisted host resolving to private: allowed
      - allowlisted host resolving to the metadata address: refused
    - stdio: `/usr/bin/npx` refused, `npx` allowed, `bash` refused
    - Write the matrix as evidence in task 9.2 (runbook evidence package: SSRF matrix)
    - _Requirements: 6.4, 6.5, 11.1_

- [x] 3. Broker client (dispatch)
  - [x] 3.1 Implement `BrokerClient` **[product-code]**
    - Create `gateway_v2/gateway_v2/dispatch/mcp_broker.py`
    - Build the `SandboxRpcRequest` exactly as `services/mcp-broker/src/sandbox/routes.py:102-124` defines it:
      - stdio sends `stdio{command, args, env}`
      - a remote transport sends `upstream{url, allowed_hosts=[host], headers={}, oauth_client_role="forbidden_in_sandbox"}`
      - every request sends `timeouts{init_seconds, method_seconds}` from the contract
    - POST `{broker}/v1/sandbox/{slug}/rpc` with `X-MCP-Broker-Key`, `X-Request-ID`, and `Content-Type: application/json`. Validate `slug` against the broker pattern before sending
    - Injected async client factory, defaulting to lazy `httpx`, following the `dispatch/http_provider.py` pattern. Injected `random.Random` and sleep for the backoff
    - Retry a broker 503 up to `mcp_broker_retry_max()` times, with backoff `min(cap, base * 2**n)` plus jitter. `tools/call` is retried only on connect or pool errors raised before the request was sent
    - Read the reply bounded at `mcp_max_result_bytes()` with `bound_nonstream_body`
    - Map each broker outcome to one value-code (design Component 6). Drop `_meta` from the reply. No exception text in any return value
    - Never dial an upstream. The broker URL is the only destination this module knows
    - _Design: Component 6_
    - _Requirements: 6.1, 6.2, 6.3, 6.6, 6.7, 6.8, 6.9_

  - [x] 3.2 Broker client tests **[test-file]**
    - Create `gateway_v2/tests/dispatch/test_mcp_broker.py` with a fake transport that records each request
    - Request body shape for stdio and for a remote transport, including `allowed_hosts` and `oauth_client_role`. Headers include the key and the request id
    - 503 then 200 succeeds after one retry. 503 five times maps to `mcp_broker_unavailable`
    - `tools/call` with a read error after send is not retried, and the transport saw exactly one request
    - 502 maps to `mcp_upstream_error`, a timeout to `mcp_broker_unavailable`, an oversize body to `mcp_result_too_large`, and a non-JSON body to `mcp_upstream_error`
    - A bad slug sends nothing
    - `_meta` is absent from the returned envelope
    - _Requirements: 6.1, 6.2, 6.3, 6.6, 6.8, 6.9_

- [x] 4. JSON-RPC and MCP wire (edge/wire)
  - [x] 4.1 Implement `edge/wire/jsonrpc.py` **[product-code]**
    - Pure functions:
      - `parse_message` returns a message or a protocol error. A batch list is `-32600`. The id may be a string, integer, or null, and its type is kept
      - `result(id, value)` and `error(id, code, message, data)` render JSON-RPC 2.0 bytes
    - `MCP_CODES`: value-code to `(jsonrpc_code, http_status)`, with the HTTP status read from `errors._ENVELOPE` for existing codes
    - `error.data` carries only `{code, request_id}`. The message is the declared `error_frame_for` message
    - _Design: Component 3_
    - _Requirements: 2.1, 2.2, 2.7, 2.8, 2.9, 8.1, 8.2, 8.3_

  - [x] 4.2 Add the MCP value-codes to the envelope **[product-code]**
    - Modify `gateway_v2/gateway_v2/edge/errors.py` `_ENVELOPE` and `gateway_v2/gateway_v2/edge/wire/sse.py` `ERROR_SHAPES` with the design Component 3 codes and statuses
    - The existing 10,000-iteration envelope property test picks them up. Add one parametrized render test per new code in `tests/edge/test_mcp_errors.py`
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 4.3 Implement `edge/wire/mcp.py` **[product-code]**
    - `METHODS` table from design Component 4
    - `fold(value, channel)`: a depth-first, deterministic walk that records `(path, channel, text)` for every string leaf and every object key, counts nodes and depth, and stops at the first bound breach
    - `join_runs(fold)` returns the index ranges of adjacent `content[i].text` leaves
    - `rebuild(value, fold, texts)` writes the texts back into a deep copy by path and raises on a count mismatch
    - `check_request` and `check_reply` apply the byte, depth, node, and block bounds from the contract
    - An import-time totality check: the channels this fold emits equal the `mcp:` entries in `TEXT_CHANNELS`
    - _Design: Component 4_
    - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 5.5, 9.1, 9.2, 9.4_

  - [x] 4.4 Wire tests and properties **[test-file]**
    - Create `gateway_v2/tests/edge/test_mcp_jsonrpc.py`: every protocol error class, id echo by type, and a batch refused
    - Create `gateway_v2/tests/edge/test_mcp_wire.py`:
      - **Property 1: fold and rebuild round-trip.** For random nested JSON, `rebuild(v, fold(v), [t for each leaf])` equals `v`. Replacing every text with a marker leaves no original text.
      - **Property 2: no text leaf escapes the fold.** A random canary placed at a random string leaf or key is in the fold.
      - **Property 3: bounds stop at the first breach.** Over-depth, over-node, and over-byte inputs are refused, and no partial fold is returned.
    - A unit cell for `join_runs` over two content blocks that split one value
    - _Requirements: 4.1, 5.1, 5.5, 9.1, 9.2_

- [x] 5. Registry, org map, console, audit (edge)
  - [x] 5.1 Implement `edge/mcp_registry.py` **[product-code]**
    - `McpRegistry.server(control_slug, server_slug)` returns a frozen `ServerEntry` with `needs_auth = auth_type != "none" or oauth_authorized`, or a value-code
    - `McpRegistry.tools(control_slug, server_slug)` returns a frozen `ToolSet` or a value-code
    - Fetch from control with `X-Gateway-Auth: true`, `X-Gateway-Internal-Key`, `X-Org-Slug`, and, for enabled-tools, `X-Server-Slug` and `server_slug`. Use an injected client factory and the timeout from the contract
    - RAM cache per `(org, server)` with the contract TTL. An error is never cached and is returned as `mcp_registry_unavailable`
    - `poll_versions(redis)` reads `mcp:scan_ver:{org}` and `mcp:scan_ver:{org}:{server}` for the cached keys and drops changed entries. It runs as a lifespan task, off the request path
    - _Design: Component 7_
    - _Requirements: 7.1, 7.2, 7.3, 7.5, 7.6_

  - [x] 5.2 Implement the org map **[product-code]**
    - In `edge/mcp_registry.py` or a small sibling module: parse `v2org=controlslug,...`, validate each slug against the broker pattern, and refuse a malformed map at boot
    - `OrgMap.control_slug(org_id)` returns the slug or `None`
    - _Design: Component 8_
    - _Requirements: 3.6, 7.5_

  - [x] 5.3 Implement `edge/mcp_console.py` and the audit route argument **[product-code]**
    - `mcp_console(...)` builds the stages `auth`, `rate_limit`, `input_scan`, `tool_call`, `output_guardrail` with the true `ran` and `action` for each, and calls `validate_console`
    - Modify `edge/audit_emit.py`: `emit_chat_audit(..., route="chat")`. MCP passes `route="mcp"` plus `server_slug` and `tool` in the detail. Chat callers are unchanged
    - _Design: Components 10, 11_
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x] 5.4 Registry, console, and audit tests **[test-file]**
    - Create `gateway_v2/tests/edge/test_mcp_registry.py` with a fake control transport:
      - headers sent; a server found
      - inactive, not-exposed, and needs-auth servers
      - control 500 and timeout map to `mcp_registry_unavailable`, with nothing cached
      - a TTL hit makes no second fetch
      - a changed `scan_ver` drops the entry (fakeredis)
    - The org map: valid map, unmapped org, a slug that fails the broker pattern
    - The console validates for allow, input block, broker failure, output block, and output redact, with the stage `ran` flags as the design states
    - The audit record carries route `mcp` and no argument or result text
    - _Requirements: 3.6, 7.1, 7.2, 7.3, 7.6, 10.1, 10.2, 10.3, 10.4_

- [x] 6. The MCP handler (edge)
  - [x] 6.1 Add the bounded body reader **[product-code]**
    - Modify `gateway_v2/gateway_v2/edge/asgi_sse.py`: add `read_body_bounded(receive, max_bytes)`. It stops reading past the bound and reports the breach. `read_body` is unchanged
    - _Requirements: 3.1_

  - [x] 6.2 Implement `McpRoute` and the handler **[product-code]**
    - Create `gateway_v2/gateway_v2/edge/mcp.py` following design Component 9, steps 1 to 11
    - Local `initialize`, `ping`, and `notifications/initialized` are answered before identity
    - A reply message with no `method` is folded, scanned as input, audited, and refused with `-32600`
    - Lifecycle: identity → org map → `lookup_plan(..., Surface.MCP)` → request id → admission → budget, with `RequestHolds` released once in `finally`
    - Registry gate, then request scan with `scan_deltas` and `resolver_for(..., Phase.INPUT)`, then `stage_upstream`, then rebuild from the verified deltas
    - `url_guard` or `check_stdio`, then `mark_dispatched`, then the broker
    - Reply bounds, then a scan of every reply message with `resolver_for(..., Phase.OUTPUT)` and join runs. A server-initiated request in a reply is withheld. Redact is applied per leaf, then the redacted leaves are scanned again; any remaining finding, or an `apply_decision` error, withholds the reply
    - `tools/list` drops disabled tools before the scan
    - Render the JSON-RPC result or error with the console object, then emit audit
    - Split into methods at or under 120 lines. If the module would pass 800 lines, move the reply scan to `edge/mcp_reply.py`
    - _Design: Component 9_
    - _Requirements: 1.1, 1.2, 1.3, 2.3 to 2.9, 3.1 to 3.6, 4.1 to 4.8, 5.1 to 5.8, 6.1, 7.4, 7.6, 8.1, 8.4, 8.5, 10.1, 10.2_

  - [x] 6.3 Handler tests **[test-file]**
    - Create `gateway_v2/tests/edge/test_mcp_route.py`. Drive `build_app` over `httpx.ASGITransport` with a fake broker, a fake registry, a fake resolver, and the chat lifecycle fixtures
    - Cells:
      - **Protocol:** every protocol error class with zero broker calls; local `initialize`, `ping`, and `notifications/initialized` with zero broker calls; a reply message with no method gives -32600 with zero broker calls
      - **Not found:** an unknown, inactive, or needs-auth server, and a disabled tool, each refused with zero broker calls; bare `POST /v1/mcp` returns `mcp_server_not_found`
      - **Lifecycle refusals:** identity unresolved, plan unavailable, org unmapped, budget refused, each with zero broker calls; the slot is released once
      - **Input:** an input block with zero broker calls; an input redact where the raw value is absent from the captured broker body and `[REDACTED]` is present; `resources/read` with a token in the URI is not blocked
      - **Output:**
        - a tool-result block gives `output_blocked`; a tool-result redact returns no raw value
        - `structuredContent` and `error.message` are scanned
        - a list reply with one blocked message withholds all
        - a server-initiated `sampling/createMessage` in a reply is withheld
        - a straddling span withholds
      - **LGW17-5:** a poisoned tool description in `tools/list` is decided by the plan
      - **LGW17-6:** a secret split across two content blocks is detected
      - **`tools/list` filtering:** disabled tools are absent from the reply
      - **LGW17-3 at the route:** a remote server whose URL resolves to loopback or metadata gives `mcp_destination_refused`, with zero broker calls
      - **Broker failure:** 503 after retries and 502, each a declared error, never an empty result
      - **Console and audit:** the console validates against the schema and audit carries route `mcp`
    - _Requirements: 11.1_

  - [x] 6.4 Cross-surface and concurrency properties **[test-file]**
    - In `tests/edge/test_mcp_route.py` or a sibling:
      - **Property 4: chat and MCP agree (LGW17-1).** For random fixtures drawn from the dev detectors' classes (PII, secret, injection, benign), the same text sent as a chat message and as a `tools/call` argument under the same plan gives the same input disposition.
      - **Property 5: one enablement (LGW17-2).** Turning one input rule from OFF to ENFORCE in the plan changes the MCP disposition with no other change.
      - **Property 6: two orgs never cross (local LGW17-4).** Concurrent `tools/call` from two orgs through the fake broker. Each broker request carries its own org's slug, each reply goes back to its own caller, and no audit record names the other org.
    - _Requirements: 4.7, 4.8, 11.1_

- [x] 7. Registration, boot, and the existing 501 tests
  - [x] 7.1 Register the routes **[product-code]**
    - Create `gateway_v2/gateway_v2/edge/surface_entries.py`, which builds the responses and MCP entries
    - Modify `edge/routes.py` `build_routes(chat, misc, response_store=None, mcp=None)` to splat those entries through a local import. With `mcp=None` the MCP routes stay the 501 placeholder
    - The entries are exact `("POST", "/v1/mcp")` → bare-route refusal and prefix `("POST", "/v1/mcp/")` → `McpRoute.handle`. RAG and vector stay `_placeholder`
    - `routes.py` stays at or under 800 lines. If it would not, move `build_routes` into `surface_entries.py` and re-export it
    - Modify `edge/app.py`: `build_app(..., mcp=None)` builds `McpRoute` once and starts the registry poll on lifespan
    - _Design: Components 12, 13_
    - _Requirements: 1.1, 1.4, 1.5, 1.6_

  - [x] 7.2 Dev profile and compose wiring **[product-code]**
    - Modify `edge/dev_profile.py`: build the MCP dependencies only when all of these are set: `AMF_DEV_MCP_BROKER_URL`, `AMF_DEV_MCP_BROKER_KEY`, `AMF_DEV_CONTROL_URL`, `AMF_DEV_GATEWAY_INTERNAL_KEY`, `AMF_DEV_MCP_ORG_MAP`
    - Log each one with secrets redacted. If any is missing, log that MCP stays 501
    - Modify `docker-compose.yml` `gateway-v2` env with those variables pointing at `mcp-broker:8311` and `control:8000` (8100 is the host port; inside the compose network control listens on 8000). Reuse the existing dev values of `MCP_BROKER_INTERNAL_KEY` and the gateway internal key, and set `dev-org=zeroshield`
    - Show the operator the compose diff before rebuilding
    - _Requirements: 3.6, 6.2, 7.1_

  - [x] 7.3 Update the tests that assert `/v1/mcp` is 501 **[test-file]**
    - `tests/edge/test_lgw12_app.py`, `test_gw16_routes.py`, `test_gw16_matcher_edges.py`, `test_gw16_app.py`, `test_lgw15_app.py`, `test_responses_route.py`: an MCP-configured app no longer returns 501 for MCP, and one built without MCP still does
    - RAG and vector keep their 501 assertions
    - Update the GW14b admin-ledger row for `/v1/mcp` to `build` with owner GW17, and update `tests/lint/test_admin_ledger.py` if it pins that row. The other MCP rows stay `retire`
    - _Requirements: 1.5, 1.6, 11.4_

- [x] 8. Local gate
  - [x] 8.1 Run the gate **[product-code]**
    - ruff, `mypy --strict` (default files), import-linter (3 contracts kept), and every AST gate (capacity literals, module mutable, frozen dataclasses, HTTP outside edge, tenant scale, sizes, dispatch provenance, secret headers)
    - Full `pytest`. Compare with the last recorded run: 2,512 passed with one pre-existing console-inventory failure. Record any new failure and explain it
    - `python -m lint.check_console_inventory`: the frontend and endpoint ledgers already fail on a clean HEAD. Confirm this card adds no new drift, or record what it adds
    - _Requirements: 11.4, 11.5_

  - [x] 8.2 Confirm the deferred scope was not stubbed **[test-file]**
    - No test claims LGW17-7 (C2 replay) or fleet-scale LGW17-4
    - No change under `gateway/ai_mesh_gateway/`, `services/mcp-broker/`, `control/`, or `frontend/`
    - The firewall plan key was not written
    - _Requirements: 11.3_

- [x] 9. Live check and evidence
  - [x] 9.1 Live check against the dev broker **[test-file]**
    - Rebuild `gateway-v2` and confirm the boot log shows MCP enabled
    - Run these against the live gateway; none needs a registered upstream:
      - protocol errors; local `initialize`
      - bare `/v1/mcp`; an unknown server (404)
      - a bad key gives the `shared_state_unavailable` refusal
    - If control already has an unauthenticated MCP server registered for the mapped org, also run:
      - `tools/list`
      - one clean `tools/call`
      - one `tools/call` carrying an input-block fixture, which gives zero sandbox activity per the broker log line for that request id
    - If no such server is registered, record "not run" and ask the operator before registering one
    - Record the plan key fingerprint before and after
    - _Requirements: 11.2_

  - [x] 9.2 Write the evidence package and the v1 behaviour ledger **[test-file]**
    - Create `docs/plans/evidence/2026-10-27-gw17/` with:
      - the SSRF matrix from 2.2
      - the cross-surface parity result from Property 4
      - the single-flag result from Property 5
      - the two-org run from Property 6
      - the description-scan and split-secret cells
      - the live check output
    - Write `v1-behaviour-ledger.json`. It registers each deliberate v1 difference in the design table as an expected entry: detection default, Tier-2 flags, unmaskable redact, scanner failure, registry failure, authenticated upstream, gateway SSRF, the allow-internal override, split-secret handling, the org source, the dropped routes, and the missing per-key allowlist and cap. This follows the runbook rule that a v1 hardening behaviour may be dropped only through a pre-registered ledger entry
    - The replay report is marked deferred (LGW17-7)
    - _Requirements: 11.1, 11.3_

  - [x] 9.3 Runbook record, after operator review only **[test-file]**
    - Once the operator has reviewed the local proof:
      - update the GW17 index row and the 2026-10-22 correction note
      - fix the runbook summary line that already lists GW17 and GW18 as done (around line 160) so it matches the index
      - record the gaps: authenticated upstreams, per-key controls, surface filtering, the inherited sandbox holes, and the untestable SSE interleave
    - _Requirements: 11.6_

## Task dependency graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1", "4.1"] },
    { "id": 2, "tasks": ["2.2", "3.1", "4.2", "4.3"] },
    { "id": 3, "tasks": ["3.2", "4.4", "5.1", "5.2", "5.3"] },
    { "id": 4, "tasks": ["5.4", "6.1"] },
    { "id": 5, "tasks": ["6.2"] },
    { "id": 6, "tasks": ["6.3", "6.4", "7.1"] },
    { "id": 7, "tasks": ["7.2", "7.3"] },
    { "id": 8, "tasks": ["8.1", "8.2"] },
    { "id": 9, "tasks": ["9.1", "9.2"] },
    { "id": 10, "tasks": ["9.3"] }
  ]
}
```

Wave 0 adds the channels and bounds every later module reads. The SSRF guard and the JSON-RPC codec need only the domain and runtime pieces. The broker client needs the bounds. The MCP wire needs the channels. The registry, org map, console, and audit changes build on the wire and codec. The handler waits for all of them. Registration and the handler tests land together. The dev wiring and the 501 test updates follow registration. The gate runs before the live check, and the runbook record waits for operator review.
