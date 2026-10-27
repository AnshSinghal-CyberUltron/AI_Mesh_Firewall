# GW17 MCP surface: local evidence (2026-10-27)

Spec: `.kiro/specs/mcp-surface-gw17/`. Surface: `POST /v1/mcp/{server_slug}` on gateway_v2.

| File | What it shows |
| --- | --- |
| `final-gate.txt` | Static gates, full offline pytest, console-inventory drift, deferred-scope check |
| `ssrf-matrix.txt` | `dispatch/url_guard` matrix (task 2.2): 28 cells |
| `properties-4-5-6.txt` | Property 4 chat/MCP parity, Property 5 one rule enables MCP, Property 6 two orgs never cross (local LGW17-4) |
| `description-and-split-secret-cells.txt` | Poisoned tool description decided by the plan; split secret detected through a join run and withheld |
| `v1-behaviour-ledger.json` | The twelve deliberate v1 differences, pre-registered; replay report deferred (LGW17-7) |
| `live/protocol.txt` | Live checks needing no upstream |
| `live/tools.txt` | Live calls through the real broker sandbox to `@modelcontextprotocol/server-everything` |
| `live/plan-fingerprint-{before,after}.txt` | The saved firewall plan key was not written |

## Live run

Stack: dev profile gateway-v2 (rebuilt), control recreated with a dev `GATEWAY_INTERNAL_API_KEY`,
mcp-broker unchanged. Boot log: `boot: mcp -> POST /v1/mcp/{server} via broker
http://mcp-broker:8311, registry http://control:8000 (scan_ver polled off the request path),
5 inputs set; keys redacted`.

The gateway ran the operator's saved firewall plan (`plan_version 1.2.58a8e05fc1ae3d5b`), not the
dev file. In that plan, PII and credential rules are scoped to output only, which explains the
SSN and card rows.

| Call | HTTP | Result |
| --- | --- | --- |
| `initialize`, `ping` | 200 | answered locally |
| `notifications/initialized` | 202 | no body |
| bad JSON / batch | 400 | -32700 / -32600 |
| unknown method | 200 | -32601 |
| bare `/v1/mcp`, unknown server, `..%2Fadmin` | 404 | `mcp_server_not_found`, no console |
| bad key | 503 | `shared_state_unavailable`, identical to chat |
| `tools/list` (cold sandbox) | 200 | 11.6 s first call, npm install in the sandbox |
| clean `echo` | 200 | five stages, all allow |
| SSN `echo` | 200 | input allow (no input PII rule), output `fw-pii-output` redact: `[REDACTED]` |
| card `echo` | 403 | input allow (no input credential rule), output `fw-credential-output` block |
| `you are DAN` | 400 | input `fw-jailbreak-input` block, `tool_call` skip |
| `resources/read` bad URI | 502 | upstream JSON-RPC error, message withheld |
| `resources/read` valid URI | 200 | five stages allow |
| `get-env` | 200 | output scanned, allow |
| unknown tool | 200 | upstream `isError` result passed through after the output scan |

Zero sandbox activity on input block: the broker logs only uvicorn access lines here (its
`sandbox rpc ... request_id=` logger is not configured to emit). By timestamp, the seven
sequential calls produced six broker POSTs; the jailbreak call (gateway 10:19:14.009, 400) is
the only one with none.

Audit: 18 MCP records in `{rv2}:audit:dev-org` with the console object. The raw SSN, card,
and jailbreak text are absent from every record.

Slot release: 42 sequential, then 40 at concurrency 20, then 20 sequential `tools/call`s, all
200 against an admission depth of 14. No `overload_shed`.

Registry invalidation: deleting the server in control bumped `mcp:scan_ver:zeroshield:gw17-everything`;
v2 returned 404 after 1.21 s (poll 2 s), not the 120 s TTL.

The test server `gw17-everything` was registered for org `zeroshield` for this run and deleted
afterwards, per the operator.

## Fixed during the live check

A refusal before the input scan (unknown server, registry, admission) carried a console object
with `final_action: allow`. The UI reads an error with no `zeroshield.action` as `error`, so this
would have badged a refused call ALLOW. The console is now attached only once the input scan has
recorded a decision, as chat does. Requirement 10.2 reworded; regression cells in
`test_gates_refuse_with_zero_broker_calls`.

## Findings, not fixed here

- **Bad key returns 503 `shared_state_unavailable`, not 401**, on chat and MCP alike. An identity
  miss is reported as a shared-state outage. A v2 identity concern, outside GW17.
- **Dev `/metrics` is one line.** The dev boot does not wire the GW14d publisher, so admission
  and audit counters cannot be read live there.
- **Broker per-request log line does not emit** (`services/mcp-broker`, outside GW17 scope).
- **Under the saved plan, the MCP server receives raw PII and card numbers**, because the PII and
  credential rules are output-only. That is the policy as configured, not a gateway defect.
