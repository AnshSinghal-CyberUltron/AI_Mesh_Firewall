# Requirements Document

## Introduction

This feature is card **GW17** of the AI Mesh Firewall v2 backend rewrite: ship the MCP surface on the same plan and resolver as chat. Today `POST /v1/mcp` is a registered placeholder in `gateway_v2/gateway_v2/edge/routes.py` that returns HTTP 501, and `gateway_v2/gateway_v2/edge/mcp.py` is empty.

v1 serves MCP from `gateway/ai_mesh_gateway/mcp_proxy.py` (about 6,250 lines) and eight sibling modules. Its security decisions do not share code with chat. It has its own posture system, its own resolver, and two rules that are the opposite of chat's: "redact never escalates to block", and a Tier-2 scanner that fails open in policy-only mode. Built-in detection is off by default, and Tier-2 needs two independent flags. Its isolation work is the part the runbook says must survive. The broker (`services/mcp-broker/`) runs one sandbox container per org. The sandbox agent enforces the upstream host allowlist, a DNS-resolving SSRF check, the stdio command and package allowlists, and no-redirect. v1 reaches it with one call, `POST {MCP_BROKER_URL}/v1/sandbox/{org}/rpc`, authenticated by `X-MCP-Broker-Key`.

This card brings MCP onto the shipped v2 lifecycle. Identity, the one plan read, admission, budget, detection, and the resolver are the ones chat uses. The broker and sandbox are reused through their existing API and are not rebuilt.

### Decisions recorded for this card

These were chosen by the operator on 2026-10-27 and are inputs to every requirement below.

- **Rules.** An MCP request is decided by the same plan rules as chat. `Rule.surfaces` is not applied on the MCP request path, the same as embeddings today. Enforcing surface filtering on every surface is a separate GW05 follow-up and is not part of this card.
- **Route.** The tenant-facing route is `POST /v1/mcp/{server_slug}`, a JSON-RPC 2.0 endpoint. The org comes from the API key only. It is never read from the URL or the body.
- **Server registry.** The gateway learns an org's MCP servers and enabled tools from control's existing endpoints (`GET /api/mcp-connector/servers/` and `GET /api/mcp-connector/internal/enabled-tools/`) with the internal key, and caches the result. Control is not changed by this card.
- **Methods.** `tools/list` and `tools/call` are in scope, plus the methods register item C33 names: `resources/read`, `prompts/get`, `completion/complete`, interleaved `notifications/progress`, `structuredContent`, `sampling/createMessage`, and elicitation.

### Scope boundaries

- **In scope.** `POST /v1/mcp/{server_slug}`. JSON-RPC parsing and rendering. Scanning of request and result text through the shared detectors and resolver. Forwarding through the broker. The server-registry client. Request and result bounds. Audit and the console object for MCP. The dev-profile wiring needed to test it locally.
- **Reuse, do not rebuild.** The identity resolver, the plan lookup, admission, the budget lease, `RequestHolds`, the input scan helpers, `PlanResolver`, `apply_decision`, the Error_Envelope value-codes, and the broker with its sandbox agent.
- **Out of scope.** Five areas:
  - **Other v1 MCP routes.** The bare REST routes (`/gateway/{org}/mcp/{server}/tools[/call]`), the ext-proxy (`/v1/mcp/ext-proxy/...`), the OAuth routes, and the internal routes (discover-tools, tools-call, oauth-token-mirror). Their GW14b admin-ledger rows stay `retire`.
  - **Per-key tool controls.** The per-key tool allowlist and per-key tool-call cap: the v2 key record (`Principal`) has no field for them. That is recorded as a gap, not faked.
  - **Other cards.** GW18 (RAG and vector), and any change to v1 `gateway/ai_mesh_gateway/`.
  - **Broker code.** Any change to the broker or sandbox code.
  - **Control code.** Any change to control.

### Deferred live gates

The operator has no cloud resources for this step. These are named here. They are not scaffolded as local passes:

- **LGW17-4 at fleet scale.** Two orgs making concurrent tool calls to the same upstream through real separate sandboxes. A local run against the dev broker is allowed when the broker is up, and is recorded as local evidence only.
- **LGW17-7.** The C2 MCP replay.
- **Two-tenant live proof.** The dev profile has one bearer key and one org, so a live two-tenant proof needs a second dev identity or a cloud run.

## Glossary

- **MCP_Surface**: The handler for `POST /v1/mcp/{server_slug}`. It lives in `edge/mcp.py` and is registered from `edge/routes.py`.
- **JSON-RPC_Message**: One JSON-RPC 2.0 object (`jsonrpc`, `id`, `method`, `params`, or `result` / `error`). A request body is one message.
- **Server_Registry**: The cached view of one org's MCP servers from control: slug, transport, URL or stdio command, active flag, and enabled and known tools.
- **Broker_Client**: The v2 caller of the broker's `POST /v1/sandbox/{org}/rpc` endpoint.
- **Broker_Slug**: The org identifier the broker accepts: 1 to 64 characters from `[a-zA-Z0-9_.-]`, with no leading or trailing `-`.
- **Scanned_Field**: A JSON value the surface turns into `(channel, text)` pairs for detection, for example `params.arguments` or `result.content`.
- **Text_Leaf**: A string value at any depth inside a Scanned_Field, including object keys that carry text.
- **Console_Object**: The `zeroshield` and `pipeline_trace` objects attached to a response, as the GW14b contract defines.
- **Error_Envelope**: The `edge/errors.py` value-code table. The MCP surface keeps its codes and statuses and renders them as JSON-RPC errors.
- **FAIL_OPEN**: Forwarding a request, or returning a result, when a guard or a decision cannot be computed. Forbidden. FAIL_OPEN stays 0.

## Requirements

### Requirement 1: One JSON-RPC route per server, org from the key

**User Story:** As an MCP client, I want one endpoint per registered server that speaks JSON-RPC, so that I can point a stock MCP client at the gateway with only the URL and the key changed.

#### Acceptance Criteria

1. WHEN a client calls `POST /v1/mcp/{server_slug}`, THE MCP_Surface SHALL handle the request and SHALL NOT return HTTP 501.
2. THE MCP_Surface SHALL take the org only from the resolved API-key identity. It SHALL NOT read an org from the path, the query, a header other than the credential, or the body.
3. WHEN `server_slug` is empty, contains `/`, or does not name an active server in that org's Server_Registry, THE MCP_Surface SHALL return a declared not-found error and SHALL make zero broker calls.
4. WHEN a client calls `POST /v1/mcp` with no server slug, THE MCP_Surface SHALL return a declared error and not HTTP 501.
5. THE route table SHALL keep `POST /v1/rag/query` and `POST /v1/vector/query` as the existing HTTP 501 placeholders.
6. THE MCP_Surface SHALL NOT register the bare REST, ext-proxy, OAuth, or internal MCP routes. Those paths SHALL keep their current behaviour and their GW14b admin-ledger rows SHALL stay `retire`.

### Requirement 2: Parse JSON-RPC and answer the protocol methods

**User Story:** As an MCP client, I want malformed messages and protocol handshakes answered the way the JSON-RPC and MCP specs describe, so that my client does not hang or misread an error.

#### Acceptance Criteria

1. IF the body is not valid JSON, THEN THE MCP_Surface SHALL return a JSON-RPC parse error (-32700) with `id` null and SHALL make zero broker calls.
2. IF the body is valid JSON but not a single JSON-RPC 2.0 message object, THEN THE MCP_Surface SHALL return an invalid-request error (-32600) and SHALL make zero broker calls. A batch array SHALL be refused the same way, not partly processed.
3. WHEN the method is `initialize`, THE MCP_Surface SHALL answer it locally with the gateway's own server information and SHALL NOT forward it upstream. This matches v1 and keeps the upstream's unscanned `instructions` text away from the client.
4. WHEN the method is `ping`, THE MCP_Surface SHALL answer it locally. WHEN the message is the notification `notifications/initialized`, THE MCP_Surface SHALL accept it with no response body and no broker call.
5. WHEN the method is `tools/list`, `tools/call`, `resources/read`, `prompts/get`, or `completion/complete`, THE MCP_Surface SHALL forward it under Requirements 4 to 6.
6. WHEN the message is a JSON-RPC reply with `result` or `error` and no `method`, for example a client's answer to `sampling/createMessage` or elicitation, THE MCP_Surface SHALL scan it as input so its findings are audited, and SHALL then refuse it with invalid-request (-32600) and zero broker calls. The broker has no channel for it and requires a `method` on every call (design change 1, accepted 2026-10-27).
7. WHEN the method is anything else, THE MCP_Surface SHALL return method-not-found (-32601) and SHALL make zero broker calls.
8. IF `params` has the wrong shape for its method, for example a non-object `arguments` on `tools/call`, THEN THE MCP_Surface SHALL return invalid-params (-32602) and SHALL make zero broker calls.
9. Every JSON-RPC response SHALL echo the request `id` exactly, including its type.

### Requirement 3: The shipped lifecycle, before any broker call

**User Story:** As a security engineer, I want an MCP call to pass the same identity, plan, admission, and budget checks as chat before anything is forwarded, so that MCP is not a way around tenant controls.

#### Acceptance Criteria

1. THE MCP_Surface SHALL read the body with a byte bound from the ResourceContract and SHALL refuse a body over that bound before decoding it.
2. THE MCP_Surface SHALL resolve identity through the same identity resolver as chat. WHEN identity does not resolve, it SHALL return the same `shared_state_unavailable` refusal chat returns and SHALL make zero broker calls.
3. THE MCP_Surface SHALL make the same single plan read chat makes. WHEN the plan does not resolve, it SHALL refuse with `plan_unavailable` and make zero broker calls.
4. THE MCP_Surface SHALL pass admission and the budget lease. It SHALL charge the request token estimate over the scanned request text. A shed or a budget refusal SHALL be returned before any broker call.
5. THE MCP_Surface SHALL release the admission slot and refund an unspent budget charge exactly once per request, through `RequestHolds`. The refund SHALL apply only when no broker call was made.
6. THE MCP_Surface SHALL map the v2 org id to the Broker_Slug and to control's org slug through a declared mapping. IF the org id is unmapped or not a valid Broker_Slug, THEN it SHALL refuse and make zero broker and registry calls.

### Requirement 4: Scan request text before forwarding

**User Story:** As a security engineer, I want every text field a client sends to an MCP server to be scanned under the plan, so that a prompt injection or a secret in a tool argument is not forwarded blindly.

#### Acceptance Criteria

1. WHEN the method is `tools/call`, THE MCP_Surface SHALL scan every Text_Leaf of `params.arguments` as input.
2. WHEN the method is `prompts/get`, THE MCP_Surface SHALL scan every Text_Leaf of `params.arguments` as input. WHEN the method is `completion/complete`, it SHALL scan `params.argument.value` and every Text_Leaf of `params.context.arguments`.
3. WHEN the method is `resources/read`, THE MCP_Surface SHALL NOT scan the request `uri` as prompt text. This matches v1, because a URI carrying an auth token would otherwise be a false block.
4. THE scan SHALL use the same detectors and the same `PlanResolver(plan, Phase.INPUT)` as chat. THE MCP_Surface SHALL NOT have its own enablement flag, its own detection default, or its own resolver.
5. IF the decision is block, THEN THE MCP_Surface SHALL return a JSON-RPC error carrying the `input_blocked` value-code and SHALL make zero broker calls.
6. IF the decision is redact, THEN THE MCP_Surface SHALL forward the masked values only. It SHALL verify that no redacted span's raw bytes remain in the forwarded body. IF a redaction cannot be applied or verified, THEN it SHALL refuse with `input_unmaskable` and make zero broker calls.
7. WHERE the same plan and the same text are sent through chat and through MCP tool arguments, THE MCP_Surface SHALL produce the same disposition the chat input scan produces (LGW17-1).
8. WHEN a plan has an input rule enabled, THE MCP_Surface SHALL apply it with no second flag (LGW17-2).

### Requirement 5: Scan results and tool descriptions before returning them

**User Story:** As a security engineer, I want everything an MCP server sends back to be scanned before the client sees it, so that a poisoned tool description or a leaked secret in a tool result does not reach the agent.

#### Acceptance Criteria

1. WHEN the method is `tools/list`, THE MCP_Surface SHALL scan every tool's `name`, `description`, and every Text_Leaf of `inputSchema` as output (LGW17-5).
2. WHEN the method is `tools/call`, THE MCP_Surface SHALL scan every Text_Leaf of `result.content`, `result.structuredContent`, and, on an error reply, `error.message` and `error.data`.
3. WHEN the method is `resources/read`, `prompts/get`, or `completion/complete`, THE MCP_Surface SHALL scan every Text_Leaf of `result`, or of `error` on an error reply.
4. THE MCP_Surface SHALL scan every JSON-RPC message in the broker reply, treating a list reply as several messages. A block on any one SHALL withhold the whole reply. A server-initiated request in the reply, such as `sampling/createMessage` or elicitation, SHALL be scanned and withheld, never relayed. The broker returns one envelope per call, so an SSE interleave of `notifications/progress` is not observable through this route and SHALL be recorded as untested (design change 2, accepted 2026-10-27).
5. WHEN a result carries more than one text block, THE MCP_Surface SHALL also scan the boundary between adjacent blocks, so that a secret split across two blocks is detected (LGW17-6, parity with v1's split-secret floor).
6. THE scan SHALL use `PlanResolver(plan, Phase.OUTPUT)` and `apply_decision`. IF the decision is block, THEN the result SHALL be withheld and the client SHALL receive a JSON-RPC error carrying `output_blocked`.
7. IF the decision is redact, THEN THE MCP_Surface SHALL return the masked values only, and SHALL verify that no redacted span's raw bytes remain in the returned body. IF a redaction cannot be applied or verified, THEN it SHALL withhold the result.
8. WHEN `tools/list` returns tools, THE MCP_Surface SHALL return only tools that the Server_Registry marks enabled for that server.

### Requirement 6: Forward only through the broker

**User Story:** As a security engineer, I want every upstream call to go through the per-org sandbox, so that the isolation work v1 built is kept and no fallback dials an upstream directly.

#### Acceptance Criteria

1. THE MCP_Surface SHALL reach an upstream only through `POST {broker}/v1/sandbox/{Broker_Slug}/rpc`. It SHALL NOT dial an MCP upstream directly under any condition, including broker failure.
2. THE Broker_Client SHALL authenticate with the broker key header and SHALL send the gateway request id as `X-Request-ID`.
3. FOR a remote transport, THE Broker_Client SHALL send `upstream.allowed_hosts` containing exactly the host of the registered URL, and `oauth_client_role` `forbidden_in_sandbox`.
4. BEFORE forwarding a remote transport, THE MCP_Surface SHALL check the registered URL host. It SHALL refuse loopback, link-local, private, reserved, multicast, unspecified, and cloud-metadata addresses, and it SHALL refuse when DNS resolution fails. This is a gateway-side check in addition to the sandbox agent's own check (LGW17-3, parity or better).
5. FOR a stdio transport, THE MCP_Surface SHALL refuse a path-qualified command, or a command outside a declared allowlist, before any broker call.
6. THE Broker_Client SHALL retry a broker HTTP 503 a bounded number of times with backoff. It SHALL NOT retry `tools/call` after the request may have been sent.
7. Every timeout, retry count, backoff, and response-size bound SHALL come from the ResourceContract. No capacity literal SHALL appear outside `runtime/resources.py`.
8. IF the broker is unreachable, returns an error, times out, or returns a reply over the size bound, THEN THE MCP_Surface SHALL return a declared JSON-RPC error. It SHALL NOT fabricate an empty tool list or an empty result.
9. THE MCP_Surface SHALL NOT pass a broker or upstream exception message to the client.

### Requirement 7: Server registry from control

**User Story:** As an operator, I want the gateway to use the MCP servers I registered in the console, so that the same registry drives v1 and v2.

#### Acceptance Criteria

1. THE Server_Registry client SHALL read an org's servers and enabled tools from control's existing endpoints, authenticated with the gateway internal key.
2. THE Server_Registry SHALL cache results per org with a bounded TTL from the ResourceContract. It SHALL treat a changed `mcp:scan_ver:{org}` or `mcp:scan_ver:{org}:{server}` value as an invalidation.
3. IF control is unreachable or returns an error, THEN THE MCP_Surface SHALL refuse with a declared error and SHALL make zero broker calls. It SHALL NOT treat an unreachable registry as an empty registry.
4. WHEN a `tools/call` names a tool that the registry marks disabled, or that is not in a non-empty known-tool set, THE MCP_Surface SHALL refuse before any broker call.
5. THE Server_Registry client SHALL send control the mapped control org slug from Requirement 3.6, never a client-supplied org.
6. IF a server's registration needs upstream credentials (`auth_type` other than `none`, or `oauth_authorized` true), THEN THE MCP_Surface SHALL refuse with `mcp_upstream_auth_unsupported` and make zero broker calls. Control does not return those credentials, so the gateway SHALL NOT call such an upstream anonymously. Credential forwarding is a later card (decision of 2026-10-27).

### Requirement 8: One decision vocabulary, rendered as JSON-RPC

**User Story:** As an MCP client developer, I want refusals as JSON-RPC errors with a stable code, so that my client can tell a policy block from a broken server.

#### Acceptance Criteria

1. Every refusal from the MCP_Surface SHALL be a JSON-RPC 2.0 error object with the request `id`. `error.data.code` SHALL carry the v2 value-code, for example `input_blocked`, `output_blocked`, `plan_unavailable`, or `budget_unavailable`.
2. THE mapping from v2 value-code to JSON-RPC `error.code` and HTTP status SHALL live in one declared table. Each value-code SHALL keep the HTTP status the Error_Envelope already gives it.
3. Error messages SHALL be the declared messages. They SHALL NOT include matched text, upstream exception text, or a stack trace.
4. WHERE the decision is flag or monitor, THE MCP_Surface SHALL forward and return normally and SHALL record the findings in the Console_Object and audit.
5. IF a scan, a resolver decision, a redaction, or a verification raises or cannot be computed, THEN THE MCP_Surface SHALL fail closed with `scan_failure` and keep FAIL_OPEN at 0. Unlike v1, a redaction that cannot be applied blocks, and a scanner failure blocks. Both differences SHALL be recorded as deliberate.

### Requirement 9: Request and result bounds

**User Story:** As an operator, I want a hostile or broken MCP server or client to hit a bound instead of exhausting gateway memory or CPU.

#### Acceptance Criteria

1. THE MCP_Surface SHALL bound the scanned argument size, nesting depth, and node count, and SHALL refuse a request over any bound before scanning.
2. THE MCP_Surface SHALL bound the broker reply size, nesting depth, node count, and number of content blocks, and SHALL withhold a reply over any bound.
3. Every bound SHALL come from the ResourceContract.
4. A refusal under this requirement SHALL be a declared JSON-RPC error, not a 500.

### Requirement 10: Audit and console object for MCP

**User Story:** As a console operator, I want an MCP call to leave the same kind of record as chat, so that Scan Detail can show what was scanned and decided.

#### Acceptance Criteria

1. THE MCP_Surface SHALL emit one audit record per phase that ran, through the shipped audit callback, with route `mcp`, the server slug, the tool name where there is one, and the deciding rules.
2. THE MCP_Surface SHALL attach a Console_Object to every response and refusal once the input scan has recorded a decision, as chat does. A refusal before that point (admission, budget, registry, tool allow-list) SHALL carry no Console_Object, because the console reports decisions and the UI reads an error with no `zeroshield.action` as `error`, not ALLOW (GW14b plan §3). Its stages SHALL be only the stages the MCP_Surface actually runs. It SHALL NOT add chat-only stages such as `model_routing`, and it SHALL NOT fill a stage that did not run.
3. THE Console_Object and the audit record SHALL validate against the GW14b console schema.
4. THE MCP_Surface SHALL NOT write `Finding.evidence`, a raw argument value, or a raw result value into the audit record, the Console_Object, or any log line.

### Requirement 11: Local proof, and the gates that stay deferred

**User Story:** As a maintainer, I want an in-process proof of each LGW17 behaviour the local machine can show, so that the card can be reviewed before any cloud run.

#### Acceptance Criteria

1. In-process tests with a fake broker and a fake registry SHALL cover each of these:
   - Every JSON-RPC error class in Requirement 2.
   - An input block with zero broker calls.
   - An input redact with the raw bytes absent from the forwarded body.
   - An output block and an output redact.
   - A poisoned tool description (LGW17-5) and a split secret across two result blocks (LGW17-6).
   - Chat and MCP disposition parity on one fixture (LGW17-1), and one rule enabled with no second flag (LGW17-2).
   - The gateway-side SSRF refusals for loopback, link-local, and metadata hosts (LGW17-3).
   - Registry failure and broker failure.
   - Two orgs running concurrently through the fake broker with no cross-org slug, result, or audit leakage (the local half of LGW17-4).
2. WHERE the dev broker is running, a live check against it SHALL be run and recorded as local evidence. It SHALL NOT be reported as LGW17-4 at fleet scale.
3. LGW17-7 (C2 MCP replay) and the fleet-scale LGW17-4 SHALL remain deferred and SHALL NOT be stubbed as local passes.
4. THE shipped chat, completions, responses, and embeddings tests SHALL still pass. THE tests that assert `/v1/mcp` returns 501 SHALL be updated to the new behaviour. Their RAG and vector 501 assertions SHALL stay.
5. THE MCP_Surface SHALL keep the import-linter contracts, the AST gates, `mypy --strict`, and ruff. No new module SHALL exceed 800 lines and no new function SHALL exceed 120 lines.
6. THE card SHALL NOT be marked done in the runbook until a human reviews the local proof.

## Recorded gaps (not fixed by this card)

- **Surface filtering is not applied.** `lookup_plan` drops the surface and `resolve()` ignores `Rule.surfaces`. MCP, like embeddings, gets the chat rules. This is a decision for this card, not an accident, and a GW05 follow-up should enforce it everywhere at once.
- **Authenticated MCP servers are refused.** Control's server list never returns tokens, passwords, or header values, so only unauthenticated remote servers and stdio servers work in this card.
- **Per-key tool controls are missing.** v1's per-key tool allowlist and per-key tool-call cap have no v2 equivalent, because `Principal` carries no such fields. An MCP key in v2 can call every enabled tool on its org's servers.
- **v1's sandbox isolation has known holes.** This card reuses the broker and sandbox, so it inherits them:
  - DNS rebinding is only partly closed, because the agent checks the resolved IP but does not connect to it.
  - `MCP_AGENT_ALLOW_INTERNAL_HOSTS` disables the agent's SSRF check for any non-empty value.
  - Per-org networks allow outbound NAT.
  - The broker key is one shared secret, compared in non-constant time.
  - A missing gVisor runtime falls back to runc unless it is marked required.

  The gateway-side check in Requirement 6.4 narrows the first two for remote transports. It does not close them.

## Requirement trace

| Runbook item | Where it is required |
|---|---|
| JSON-RPC framing in `edge/mcp.py`, security through the shared plan and resolver | Requirements 1, 2, 4, 5, 8 |
| Collapse the double gate: one plan, one enablement | Requirements 4.4, 4.8 |
| Port sandbox, broker, SSRF and allowlist logic | Requirements 6, 7 |
| Tool descriptions are scanned text | Requirement 5.1 |
| Upstream unreachable is a declared error, never a fabricated empty list | Requirements 6.8, 7.3 |
| Sandbox exhausted: bounded queueing, never direct dialling | Requirements 6.1, 6.6, 6.7 |
| C33: resources/read, prompts/get, progress interleave, structuredContent, sampling, elicitation | Requirements 2.5, 2.6, 4.2, 5.2 to 5.4 |
| LGW17-1 to LGW17-6 | Requirement 11.1 |
| LGW17-4 fleet scale, LGW17-7 | Requirement 11.3, deferred |
| GW14b console contract | Requirement 10 |
