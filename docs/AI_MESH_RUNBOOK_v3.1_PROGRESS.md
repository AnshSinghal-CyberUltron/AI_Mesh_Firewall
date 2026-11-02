# Runbook v3.1 progress

Status date: 2 November 2026. Source of the card text: `docs/AI_MESH_MASTER_RUNBOOK_v3.1_CLEAN.docx`, Part 00. This file does not change that document.

GW26 has new code on this tree and has been exercised on the local stack. It does not exit. The live lines below say exactly what was observed and what was not. GW17b, GW17c, GW27, GW28, GW29, GW30, GW31, GW32, GW33, GW34, GW35, and GW36 have no new card work from this pass. The runbook says each of those cards is required before GW21 and GW23, and that a card does not exit on unit tests alone.

## GW26 — Customer policy rules and surface scoping

Depends on GW05 and GW07. Required by GW18, GW27, GW31, GW21, and GW23.

Objective in the runbook: restore Policy Management so an organization can write keyword, regex, pattern, and semantic conditions, scope them to global, chat, RAG, vector, MCP, embeddings, or a named pipeline, and have that scope enforced on every matching surface.

### Implementation work

| Runbook item | State | What is true in the tree |
|---|---|---|
| Extend `Rule` with a condition (type, field, direction, pattern or keyword list, detector class), compile it once into the matcher, and fail closed on a regex timeout | In the gateway | `Rule.condition` is optional and defaults to none, so older plans still hash. Keyword match is a case-insensitive substring. A nested quantifier, a quantified wildcard, a quantified alternation, or a pattern that does not compile is refused at scan time with `policy_regex_timeout` (HTTP 400 `invalid_request_error`). The pattern is not executed. The plan still loads. |
| Enforce surfaces in the resolver, and add a console control to set them | Partial | There is no `GLOBAL` surface. A rule with no surface list uses the domain default: pipeline → chat and embeddings, rag → rag and vector, mcp → mcp. An explicit list replaces that default. The Policy Management rule form has a surface checklist. The resolver skips a finding when the route surface is not in the rule's list. `scan_conditions` now takes the same surface and skips a refused pattern that does not name it. Chat input, embeddings, MCP input and output, the non-streaming chat output scan, and the streaming chat detector pass their surface. Omitting the surface still selects by phase. A rule with `pipeline_stage` set to `query`, `retriever`, `ranker`, or `generator` matches only that stage. On 2 November a temporary block keyword `Gw26StageQuery` with stage `query` was published (169 rules) and a chat call returned HTTP 200 `allow` with the keyword present. The rule was deleted and the org republished at 168. No live route passes a stage name, so this card did not observe the rule firing. That positive case waits on GW31. A completed customer match is held until the release contains the whole span, so a keyword whose tail looks like a card is not emitted in pieces. The echo provider's diagnostic re-scan catches `PolicyRegexRefusal` and does not withhold a request the route already allowed. |
| Publish Policy Management rules into the v2 plan | Wired for pipeline, rag, and mcp | Saving a policy or a rule republishes every enabled policy on the org, plus the firewall drafts. `rewrite` and `model_downgrade` are omitted from the document and named in `unsupported`, so a bad action cannot replace the previous plan. A compile failure leaves the previous plan serving. Unknown categories map to `exfil`. |
| Write each organization's plan to its own key | Wired, and checked with two orgs | The org key is `{rv2}:plan:{org}`. The primary org still falls back to `{rv2}:dev:firewall-plan` when its org key is empty. A peer org never reads that dev key. The dev profile accepts an optional second bearer (`AMF_DEV_PEER_API_KEY`, `AMF_DEV_PEER_ORG_ID`; compose defaults `dev-peer-key` and `org-b`). The customer matcher uses the request org's program. On 2 November the dev key was still length 1367 and sha256 `8017db3b907c550592ce910a70c9ceaf571685c7a28be794a8c7e4ecccf92aa4`. |

The 27 October decision was to ignore surfaces. The wired routes no longer do that for findings that reach the resolver. Runbook decision 10 still says "Confirm." This file records the reversal. The runbook text was not edited.

`semantic` conditions are not a separate matcher. A detector-class rule joins builtin findings only when the rule has no customer condition.

### Live data created on the zeroshield org

These rows are in Postgres and in `{rv2}:plan:dev-org`. They are not source edits.

- Policy `GW26_COMPETITOR_CHAT` (id 47), domain pipeline.
- Rule "Block AcmeRivalGW26 on chat": keyword `AcmeRivalGW26`, action block, field prompt, surface chat only.
- Rule "Redact ZsCardToken99 globally": keyword `ZsCardToken99`, action redact, field response, surface global, replacement `[REDACTED]`. This is a test token, not a card number.
- A third rule, pattern `(a+)+`, field prompt, surface list embeddings, was created for L26-4 on 2 November (rule id 169), observed, then deleted. The republish returned `rules: 168`. The pattern is not in `{rv2}:plan:dev-org`.
- `{rv2}:plan:org-b` holds one chat keyword, `OnlyOrgBgw26`. It is not a console org. The dev key was not the body of that write.

The save responses named one omitted rule: `PKG2_PIPE_UNAUTHORIZED_ADVICE/Rewrite definitive medical advice (rewrite)`.

### Live tests

L26-4 and L26-5 were observed on the live gateway. L26-3 was replayed on chat for the 16 pipeline policies. The rag and mcp packages stay registered differences. L26-1 and L26-2 are still unmet on MCP and RAG.

| Test | Runbook pass line | State |
|---|---|---|
| L26-1 | A competitor-name rule scoped to chat only. Blocked on chat; allowed on MCP and RAG | Chat only. In the Attack Simulator at `http://127.0.0.1:8180/?tab=firewall-1-1`, signed in as `admin@zeroshield.io`, prompt `Tell me about AcmeRivalGW26 pricing` was BLOCKED, HTTP 400, 19 ms, request `zs-dev-org-b6525df0a2da8f4434e67d80`. Stages: Auth ALLOW, Rate Limit ALLOW, Input Scan BLOCK, Model Routing SKIP, Model Output SKIP, Output Guardrail SKIP. The same page, prompt `what is the refund window`, was ALLOWED, HTTP 200, 25 ms, request `zs-dev-org-3317cafc4a2684a2a974b9bd`, model text `echo: what is the refund window`. Curl with model `echo-1` matched those two status codes. MCP: `MCPServerRegistration` count for this org is 0. `POST /v1/mcp` `tools/call` returned HTTP 404 JSON-RPC `-32000` "No active MCP server with that name exists for this organization." The scan did not run. RAG: `POST /v1/rag/query` returned HTTP 501 `not_implemented:rag`. A chat-only rule does not select on `Surface.RAG` in the resolver unit test. That is not a live RAG allow. |
| L26-2 | An output redact rule on all surfaces masks the value in chat, responses, MCP results, and RAG chunks | Chat, `/v1/responses`, and streaming chat. The simulator prompt `quote ZsCardToken99 please` returned HTTP 200, Output Guardrail REDACT, model text `echo: quote [REDACTED] please`. Curl on `/v1/chat/completions`, `/v1/responses`, and `stream: true` was HTTP 200 with the mask present and the raw token absent. With SSE stream mode on, the simulator showed the same mask after the stream hold fix. MCP and RAG were not claimed: no MCP server is registered, and RAG is 501. |
| L26-3 | Replay the 48 production `PKG2_*` policies. Each enforces as in v1, or is a registered expected difference | Pipeline package replayed on chat on 2 November. Rag, mcp, the missing four, the rewrite rule, monitor-on-the-wire, and the catalog replacement tokens are registered differences. Detail is under the table. |
| L26-4 | A pathological regex finishes in bounded time and fails closed with a declared code | Observed on 2 November, then removed. Rule id 169, pattern `(a+)+`, surface embeddings, field prompt. `POST /v1/embeddings` returned HTTP 400 `policy_regex_timeout` in 24.0 ms. `POST /v1/chat/completions` with `what is the refund window` stayed HTTP 200, model text `echo: what is the refund window`. The rule was deleted and the org republished (`rules: 168`). After the feed poll, the refund prompt was HTTP 200 and embeddings returned 403 `scan_failure` because echo-1 does not return a JSON vector. That 403 is not a pass. |
| L26-5 | Two orgs with conflicting rules. Each enforces only its own plan | Observed on the live gateway on 2 November. Primary bearer: `AcmeRivalGW26` HTTP 400 `input_blocked`; `OnlyOrgBgw26` HTTP 200 `echo: quote OnlyOrgBgw26 please`. Peer bearer `dev-peer-key` for org `org-b`: `AcmeRivalGW26` HTTP 200; `OnlyOrgBgw26` HTTP 400 `input_blocked`. The dev key bytes did not change. The browser is still one org. Production boot is still GW36. |

L26-3 chat replay, 2 November, model `echo-1`. The pipeline catalog is 16 policies and 55 rules. Each rule was sent once as its own chat prompt. Request times were 2.9–28.4 ms (399.4 ms summed). No probe rule was left behind, and `{rv2}:dev:firewall-plan` stayed length 1367 and sha256 `8017db3b907c550592ce910a70c9ceaf571685c7a28be794a8c7e4ecccf92aa4`.

- 23 block rules: HTTP 400 `input_blocked`, action `block`. Twenty-one were decided by a `pol-PKG2_*` rule. `Block instruction-override phrases` was decided by `fw-prompt_injection-input`. `Block known jailbreak personas` was decided by `fw-jailbreak-input`. The action matched the catalog in both of those.
- 13 redact rules: HTTP 200, action `redact`, the matched bytes absent, `[REDACTED]` present. Email, US phone, and US SSN were decided by `fw-pii-input`. The other ten were decided by a `pol-PKG2_*` rule. The catalog replacement tokens, including `[REDACTED_EMAIL]`, were not written. A direct email check had `[REDACTED]` and not `[REDACTED_EMAIL]`.
- 18 monitor rules: HTTP 200, action `allow`, phrase present, no deciding rule. They are published as mode `monitor`. The resolver records that class of finding and does not change the disposition, so the wire stays `allow`. That is a registered difference from a visible monitor tag.
- 1 rewrite rule, `Rewrite definitive medical advice`: HTTP 200, action `allow`, phrase present. It stays omitted from the plan and named in `unsupported`.
- Three fixtures match more than one catalog rule of the same action. Card PAN matches `PIPE_PII` and `PIPE_PCI`, both redact, decided by `pol-PKG2_PIPE_PII-8`. The two profanity redact rules share a fixture, decided by `pol-PKG2_PIPE_PROFANITY-39`. The shell exfil block shares a fixture inside `PIPE_CODE_EXFIL`, decided by `pol-PKG2_PIPE_CODE_EXFIL-50`.

Not replayed on a live route, and registered as that difference: 14 rag policies (49 rules) apply to rag and vector, and `POST /v1/rag/query` is HTTP 501. 14 mcp policies (56 rules) apply to mcp, and `POST /v1/mcp` is HTTP 404 with no server. The 13 `VEC_*` specs are not `PKG2_*`. The catalog has 44 policy specs and the docx says 48. The docx names no missing four, and none were invented.

Embeddings on this dev profile: every `/v1/embeddings` call, including a clean prompt and the competitor name, returns HTTP 403 `scan_failure` because echo-1 returns text and the embeddings drain requires a JSON vector. That status is not `input_blocked`. The in-process ASGI test uses a provider that yields a vector and shows a chat-only keyword is not an input block on embeddings.

Auth and Rate Limit in the simulator stayed ALLOW on the blocked request. Those two stages are still hard-coded (GW35). The page does not invent kill-switch, policy, or model-input stages.

### Unit tests that exist for this card

On 2 November the GW26 detect, plan, edge, and dev-profile files were run again: 72 passed. ruff, mypy --strict on the touched modules, and the size check passed. Django tests were not run; the repo venv has no Django.

- `gateway_v2/tests/detect/test_gw26_conditions.py`
- `gateway_v2/tests/edge/test_gw26_customer_rules.py`
- `gateway_v2/tests/plan/test_gw26_org_plan.py`
- `gateway_v2/tests/resolve/test_gw26_surface_scope.py` (resolver surface filter; it does not close L26-1)

Control Django tests were not run here. The repo virtualenv and `gateway_v2/.venv` do not include Django. The control container is the process that published the live plan.

## Cards not started

| Card | Runbook objective, short | State |
|---|---|---|
| GW17b | Authenticated MCP upstreams | Not started. Upstream auth still refuses with `mcp_upstream_auth_unsupported`. |
| GW17c | Per-key `mcp_allowed_tools` and `mcp_max_tool_calls` | Not started |
| GW27 | The 45 unsupported FirewallConfig fields, built or retired by name | Not started. `firewall_v2_map.py` still rejects those fields on save. |
| GW28 | Multi-model routing | Not started |
| GW29 | Kill switch, isolation, reroute, auto-isolation | Not started. `kill_switch_engaged` is still hard-coded false. |
| GW30 | Vector adapter and the connector list, `unsupported_operation` instead of an empty success | Not started. No vector client. |
| GW31 | Four-stage RAG, the retired routes, async ingest, project on Principal, six RAG settings | Not started. `/v1/rag/query` is HTTP 501 `not_implemented:rag`. |
| GW32 | v1 MCP URL or a signed redirect, gateway as OAuth 2.1 server, real HTTP+SSE and WebSocket | Not started |
| GW33 | Output parity: IP leak, exfil channels, grounding, human review, REWRITE, canary | Not started. The GW26 publisher omits `rewrite` and `model_downgrade` and names them unsupported. It does not implement REWRITE. |
| GW34 | SDK compatibility; unknown paths 404 `endpoint_not_found` | Not started. Unknown paths still return 501. |
| GW35 | Console pages and Scan Detail truth | Not started. The Attack Simulator still shows Auth and Rate Limit as ALLOW on the competitor block above. Those two stages are hard-coded allow. |
| GW36 | Production boot and a real local model | Not started. With `AMF_DEV_PROFILE` unset, the boot still does not serve real traffic. |

## Owner decisions still written as open in v3.1

§00.7 items 7 through 12 are unchanged: egress against the $5,000 envelope, the latency-tail trim share, whether holdback counts in the 20 ms SLO, confirming the surface-scoping reversal, the MCP client URL, and whether six-stage Scan Detail is signed. Item 10 is the one this slice touches. Findings that reach the resolver are filtered by surface, and a refused regex is skipped when the route surface is not in the rule's list. The streaming chat detector receives the route surface. The document still asks for confirmation, and this file does not pretend that sentence was signed.
