# Bugfix Requirements Document

## Introduction

Running `gateway_v2` locally through the opt-in dev profile (`AMF_DEV_PROFILE=1`, plan `gateway_v2/dev/plan.example.json`: pii → redact, secret/credential → block, prompt_injection/jailbreak/command → flag, every rule `scope: both`) exposed six enforcement defects in the clean-room gateway. Each one lets a policy the tenant selected be silently defeated (the runbook's CRITICAL impact class) or leaks a bounded resource. This is Phase 1 of a larger plan. The runbook `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` is the sole source of truth; each clause cites the section or card it comes from.

Defects in scope:

- **A. Input REDACT is not applied before the provider.** Violates §0-A.1 (byte-level provider proofs for REDACT), §10.4 step ⑥ (dispatch applies the transformations and verifies the transformed bytes before the one provider call), GW09 ("exactly one redactor… the bytes the resolver inspected are the bytes dispatch sends"; LGW09-2, LGW09-5) and C21 (PII reaches the provider raw while the trace says masked). The strict xfail `tests/edge/test_dev_profile_pipeline.py::test_input_redact_masks_pii_before_the_provider` already records this defect. `/v1/embeddings` has the same defect: `EmbeddingsSurface` acts on the input decision only when it is BLOCK, so a REDACT finding in an embedding input item reaches the provider raw. Gateway v1 masks every embedding input item before the provider and fails closed when it cannot (`_scan_redact_embedding_inputs`; v1 tests `test_m1_3_vector_firewall_sdk.py::test_embedding_input_pii_is_redacted_before_the_provider`, `test_embedding_batch_redacts_each_item_independently`, `test_e10_embeddings_scan.py`).
- **B. Card numbers (PCI PAN) are never detected.** The GW09 ruleset already has a `credential_card` pattern (category `CREDENTIAL`, as the shipped GW09 spec defines it). It never fires, because the multi-pattern pass is leftmost-first: the `pii_phone` prefilter consumes the digit run, the phone precise matcher then rejects it, and the card branch is never evaluated. The pattern also has no Luhn check. Grounded in GW09 ("pattern is a superset prefilter → verified by the precise matcher before a finding is emitted"; "false positives are scored rather than assumed"; LGW09-6).
- **C. The jailbreak phrase "you are DAN" is missed (ASCII and homoglyph spellings), and some confusables are not folded.** This corrects the bug report. Canonicalisation does fold Cyrillic `А` (U+0410), so `DАN` becomes `DAN`. The miss comes from the same matcher defect as B: the `\bDAN\b` prefilter anchors the precise confirm at the `DAN` offset, so the precise `you are DAN` can never match. The ASCII spelling fails the same way. A real coverage gap also exists: Greek confusables such as `Ι` (U+0399) are not folded. Canonicalisation is owned by GW09 (C21/C26 decoder set, which includes "Cyrillic homoglyphs"), T07 L07-2 (confusables) and GW10 C6 ("confusable mapping"), not GW07. GW07 is the resolver card.
- **D. Admission slots leak.** The GW15 request lifecycle admits through `AdmissionController.admit` but never calls `complete`. Request-queue slots are therefore never released, and in-flight records are never pruned. Grounded in GW19 (R2-07/R2-08: bounded queues with exported depth, admitted work answered and never abandoned, graceful drain over the true in-flight set) and the §10.4 lifecycle.
- **E. The resolver is not phase-aware.** One resolver bound to one phase serves both input and output decisions, so rules scoped `input` or `output` cannot be honoured, and the dev profile refuses to boot such plans. Grounded in §10.5.2 (`Rule.scope: INPUT | OUTPUT | BOTH`), §10.5.3 (`resolve(findings, plan, phase)`), GW07 and GW05/C22 (the compiler rejects only plans whose semantics the resolver cannot represent).
- **F. The budget cost unit does not match the lease unit.** GW06 sizes the lease chunk in requests (`q_safe` × refresh period) while GW15 charges a character count. A long prompt can therefore never be admitted, and the dev profile works around it by charging 1 unit per request. A request refused with zero provider calls also keeps its charge. Grounded in GW06 ("a shared lease for org-level **token** budget"), C29 ("chunk ≤ remaining budget / active workers; refund when nothing is dispatched") and R2-09.

Out of scope for this spec:

- Intent detection such as `delete_database`, which needs the GW10/GW25 semantic model on GPU.
- Decision visibility, audit, the `X-ZeroShield-*` headers and the console contract (Phase 3, GW14b).
- Real provider egress and the HttpProviderClient seams (Phase 2), and with them the C29 "true-up against actual usage", which needs provider-reported usage.
- The full `AuthorizedProviderEntry` on chat (provider pooling, audit, rewrite). Gateway v1's chat path does audit and pool providers, and v1 parity is the target, but that work belongs to Phase 2 (provider egress) and Phase 3 (GW14b audit and console). v1 does not decide the phasing.
- `/v1/responses`, MCP, RAG and vector-store surfaces. `/v1/embeddings` input REDACT is in scope under A.
- Compose wiring and GW02 parity.

## Bug Analysis

### Current Behavior (Defect)

**A. Input REDACT**

1.1 WHEN an input channel contains a value detected under an ENFORCE rule whose action is REDACT, and no BLOCK applies to the request, THEN the system forwards the raw value to the provider unchanged. Only the output guard masks it, and only on the echoed reply.

1.2 WHEN the input decision is REDACT THEN the system makes no pre-dispatch check that the outbound provider payload no longer contains the detected spans, so a REDACT that removed nothing would still be dispatched.

1.3 WHEN the redactable value sits in a system message, in a later message, or split across content parts of a message (C21) THEN the system forwards it raw in that channel.

**B. Card numbers**

1.4 WHEN the input or output contains a Luhn-valid PAN of 13–19 digits, written with no separators, single spaces or single dashes (e.g. `4111-1111-1111-1111`, `4111111111111111`, `4111 1111 1111 1111`), THEN the system emits no finding. The earlier `pii_phone` prefilter consumes the digit run, its precise matcher rejects it, and the card pattern is never evaluated, so the PAN passes unmasked and unblocked.

1.5 WHEN a digit run has the card shape but fails the Luhn checksum (e.g. `4111-1111-1111-1112`) THEN the card pattern has no checksum validation, so the only thing keeping it from being reported as a card is the shadowing defect in 1.4.

1.6 WHEN any pattern's prefilter matches a region and that pattern's precise matcher fails to confirm THEN the system evaluates no other pattern over the consumed region, so a confirmable match of a different pattern there is silently lost.

**C. Jailbreak phrase and confusables**

1.7 WHEN the input contains "you are DAN", in ASCII or with a confusable letter such as Cyrillic `А` (U+0410) in `DАN`, THEN the system emits no JAILBREAK finding. The `\bDAN\b` prefilter anchors the precise `you are DAN` confirm at the `DAN` offset, where it cannot match.

1.8 WHEN an injection or jailbreak phrase contains a Latin look-alike outside the current fold table (e.g. Greek capital Iota U+0399 in `Ιgnore all previous instructions`) THEN canonicalisation leaves it unfolded and the system emits no finding.

**D. Admission slots**

1.9 WHEN an admitted request completes successfully, whether as a non-streaming JSON response or a stream ending in `[DONE]`, THEN the system never calls `AdmissionController.complete`. The request-queue slot stays occupied and the in-flight record stays unfinished.

1.10 WHEN an admitted request then ends by budget refusal, input BLOCK, input scan failure, output BLOCK, a raised exception, client disconnect or cancellation, a kill-switch cut or a stream timeout THEN the system never calls `complete` for it.

1.11 WHEN `complete` is called with an unknown or already-completed request id THEN the system still pops a request-queue slot, releasing a slot that belongs to another in-flight request. Completed records are only marked finished and never removed, so the in-flight table grows without bound.

1.12 WHEN admitted requests accumulate without completion THEN the exported request-queue depth only ever grows and a later drain waits on requests that already finished. The dev profile works around this with an ASGI-level wrapper that completes on response return.

**E. Phase-aware resolver**

1.13 WHEN the plan contains a rule with scope `input` or `output` THEN the system resolves both input and output findings with one resolver bound to one phase. An input-only rule is applied to output (or an output-only rule to input), or the rule is dropped in the phase it targets.

1.14 WHEN the dev profile is started with a plan containing a rule whose scope is not `both` THEN the system refuses to boot.

**F. Budget cost unit**

1.15 WHEN the GW06 lease chunk is derived THEN it is sized in requests (one refresh period of `q_safe` throughput, bounded by the queue depth), while the GW15 lifecycle charges a character-count cost against it. Any prompt whose cost exceeds the chunk is refused `budget_unavailable` while the org budget still has room. The dev profile has to charge 1 unit per request instead.

1.16 WHEN the chunk is derived THEN it does not depend on the org's remaining budget or the number of active workers (C29), so one worker can lease budget that other workers cannot spend.

1.17 WHEN an admitted request has spent budget and is then refused with zero provider calls (input BLOCK, input scan failure) THEN the system keeps the spent budget and does not refund it (C29: "a policy-BLOCKED request with zero provider calls was charged").

**A. Input REDACT (embeddings surface)**

1.18 WHEN a `/v1/embeddings` input item (a string input, or any item of a list input) contains a value detected under an ENFORCE rule whose action is REDACT, and no BLOCK applies to the request, THEN the system forwards the raw item to the provider unchanged. The embeddings input scan acts only on a BLOCK disposition and ignores REDACT, and it makes no pre-dispatch check that the outbound items no longer contain the detected spans (gateway v1 parity gap: `_scan_redact_embedding_inputs` masks before the provider).

### Expected Behavior (Correct)

**A. Input REDACT**

2.1 WHEN an input channel contains a value detected under an ENFORCE rule whose action is REDACT, and no BLOCK applies to the request, THEN the system SHALL replace every detected span in the outbound provider payload with the single GW09 redaction placeholder (`[REDACTED]`) before the provider is opened. It SHALL use the one GW09 redactor, in both streaming and non-streaming modes, so the provider never receives the raw value (§0-A.1, §10.4 ⑥, GW09, LGW09-2). The strict xfail `test_input_redact_masks_pii_before_the_provider` SHALL pass with its `xfail` marker removed.

2.2 WHEN the input decision is REDACT THEN the system SHALL verify the transformed outbound bytes against the decision's finding spans before dispatch. IF any detected value remains, or the transformation cannot be applied, THEN the system SHALL fail closed: it refuses with the declared code `input_unmaskable` through the single `edge/errors` envelope, makes zero provider calls and never dispatches unverified bytes (GW09 "unmaskable path", LGW09-5, GW07 failure scenarios). `input_unmaskable` SHALL render as HTTP 400 with `error.type` `invalid_request_error`, the same family as `input_blocked`, and NOT as 403 `gateway_error`. This matches gateway v1, which fails a redaction no-op on flagged PII or secrets closed with a content-category block that `_build_safe_block_response` re-maps to `GATEWAY_BLOCK_STATUS` (default 400) so the stock OpenAI SDK raises `BadRequestError`. It also follows the frozen GW01 wire contract (§10.2.2) and the reconciliation `edge/errors.py` already applies to `input_blocked` → `(400, "invalid_request_error")`.

2.3 WHEN the redactable value sits in any forwarded text-bearing input channel, including system messages, later messages and multi-part content, THEN the system SHALL mask it in that channel, so the bytes the resolver inspected are the bytes sent (C21, GW09).

**B. Card numbers**

2.4 WHEN the input or output contains a Luhn-valid PAN of 13–19 digits with no separators, single spaces or single dashes THEN the system SHALL emit an EXECUTED finding of category `CREDENTIAL` (pattern `credential_card`, the category the shipped GW09 taxonomy assigns; no new category term is introduced). Its original-byte span SHALL cover the whole PAN including separators, and the outcome SHALL follow the plan's credential rule (dev plan: BLOCK with zero provider calls; under a REDACT rule: masked with `[REDACTED]`).

2.5 WHEN a digit run has the card shape but fails Luhn, or has fewer than 13 or more than 19 digits, THEN the system SHALL NOT emit a card finding.

2.6 WHEN one pattern's prefilter matches a region and its precise matcher fails to confirm THEN the system SHALL still evaluate every other pattern over that region, so every confirmable match of every pattern emits its finding regardless of pattern order. This SHALL hold within GW09's single canonicalisation pass and single multi-pattern match.

**C. Jailbreak phrase and confusables**

2.7 WHEN the input contains "you are DAN" in ASCII or in any spelling whose confusable letters fold to it (e.g. Cyrillic `А` U+0410) THEN the system SHALL emit an EXECUTED JAILBREAK finding whose span maps back to the original bytes.

2.8 WHEN text contains a character from the declared confusable set THEN canonicalisation SHALL fold it to its Latin look-alike before matching (GW09 decoder set, L07-2, GW10 C6). The set SHALL include at least the Cyrillic and Greek upper- and lower-case letters that look like Latin letters, and it SHALL be versioned, so a change to the set changes `detector_version`. The span map SHALL keep mapping every canonical match back to original byte offsets such that redacting the original span removes the confusable-bearing bytes (GW09 span invariant).

**D. Admission slots**

2.9 WHEN a request has been admitted THEN the system SHALL call `complete` for it exactly once, on every exit path: a successful non-streaming response, stream end (`[DONE]` or a terminal error frame), a refusal after admission (budget, input BLOCK, scan failure, output BLOCK), a raised exception, client disconnect or cancellation, a kill-switch cut and a stream timeout (GW19, §10.4).

2.10 WHEN a request was shed or refused before admission (pre-checks, parse, identity, plan, or a `ShedVerdict`) THEN the system SHALL NOT call `complete` for it.

2.11 WHEN `complete` is called for an unknown or already-completed request id THEN the system SHALL release no slot and change no queue depth. Completed records SHALL be pruned, so the in-flight table is bounded by the number of concurrently admitted, unfinished requests.

2.12 WHEN admitted requests finish under steady load THEN the exported request-queue depth SHALL return to the number of currently unfinished admitted requests, and drain SHALL wait only on those. The default boot path and the dev profile SHALL release slots through the same lifecycle, with no wrapper outside it.

**E. Phase-aware resolver**

2.13 WHEN input findings are resolved (chat input scan, and the input scan of every other shipped surface that resolves findings) THEN the system SHALL resolve them with `Phase.INPUT`, selecting only rules scoped `input` or `both`. WHEN output findings are resolved (streaming output guard, non-streaming output, mid-stream error-frame scan) THEN the system SHALL resolve them with `Phase.OUTPUT`, selecting only rules scoped `output` or `both` (§10.5.2, §10.5.3, GW07).

2.14 WHEN the dev profile is started with a valid plan containing rules scoped `input` or `output` THEN the system SHALL boot and enforce each rule only in its declared phase.

**F. Budget cost unit**

2.15 WHEN a request's budget cost is computed and when the lease chunk is derived THEN both SHALL be in one unit, tokens of the org token budget (GW06, C29). The request cost SHALL be the deterministic estimate gateway v1 uses (`LLMRouter.estimate_prompt_tokens`, `gateway/ai_mesh_gateway/llm_router.py`): input tokens = `max(1, prompt_chars // D)` when the input has characters and 0 when it has none (list content parts joined), plus the requested output budget `max_tokens × n`, with a minimum total of 1. Charging `max_tokens × n` means a high-`n` or high-`max_tokens` request cannot under-charge. `D` is the chars-per-token divisor (4 in v1) and SHALL be declared in `runtime/resources.py`. v1 pre-charges this estimate before forwarding and reconciles it with actual usage afterwards; that true-up stays out of scope until Phase 2. The chunk SHALL be derived from the org's remaining budget and active workers (at most remaining budget / active workers) and bounded by the ResourceContract (request bound × max request tokens, with the per-worker fair share binding). The chunk cap cannot be decided from v1, which has no lease and charges a per-request Redis Lua TPM bucket, so it stays the runbook-derived choice (GW06, C29). No capacity literal may appear outside `runtime/resources.py`.

2.16 WHEN a request's token cost exceeds one chunk but is within the org's remaining budget THEN the system SHALL NOT refuse it `budget_unavailable` solely because of the chunk size. It SHALL refuse with `budget_unavailable` only when the org budget, within the declared lease overshoot, cannot cover the cost.

2.17 WHEN an admitted request that spent budget is refused with zero provider calls (input BLOCK, input scan failure, input REDACT verification failure) THEN the system SHALL refund the spent cost to the lease (C29 "refund when nothing is dispatched").

2.18 WHEN the dev profile runs THEN it SHALL charge the same token cost as the default lifecycle, with no per-request unit override.

**A. Input REDACT (embeddings surface)**

2.19 WHEN a `/v1/embeddings` input item contains a value detected under an ENFORCE rule whose action is REDACT, and no BLOCK applies to the request, THEN the system SHALL replace every detected span in that item with the single GW09 redaction placeholder (`[REDACTED]`) before the provider is opened. It SHALL use the one GW09 redactor and mask each item independently, so the provider never receives the raw value (§0-A.1, §10.4 ⑥, GW09, LGW09-2; gateway v1 parity: `_scan_redact_embedding_inputs`, `test_embedding_input_pii_is_redacted_before_the_provider`, `test_embedding_batch_redacts_each_item_independently`).

2.20 WHEN the embeddings input decision is REDACT THEN the system SHALL preserve the input's shape and order: a string input stays a string, a list input stays a list of the same length in the same order, and every item with no redactable finding SHALL be forwarded byte-identical to the parsed request.

2.21 WHEN the embeddings input decision is REDACT and the transformed outbound items cannot be verified against the decision's finding spans (a detected value remains, or the transformation cannot be applied) THEN the system SHALL fail closed with `input_unmaskable`, rendered as in 2.2 (HTTP 400 `invalid_request_error`), with zero provider calls (GW09 "unmaskable path", LGW09-5; gateway v1 blocks with the provider never called).

### Unchanged Behavior (Regression Prevention)

**Enforcement outcomes**

3.1 WHEN any input finding resolves to BLOCK, including a BLOCK co-occurring with a REDACT or FLAG finding (C22, LGW07-6), THEN the system SHALL CONTINUE TO refuse with `input_blocked` before the provider is opened, with zero provider calls (e.g. `key AKIAIOSFODNN7EXAMPLE` under the dev plan).

3.2 WHEN the input has no findings, or only FLAG or MONITOR findings, THEN the system SHALL CONTINUE TO forward the input bytes to the provider byte-identical to the parsed request.

3.3 WHEN the provider's reply contains a redactable value THEN the output guard SHALL CONTINUE TO mask it, streaming and non-streaming, so the client never receives the raw value (`test_client_never_sees_raw_ssn_and_aws_key_is_blocked`).

3.4 WHEN the resolver decides THEN it SHALL CONTINUE TO be pure, to resolve each finding under its own rules with the request disposition being the most restrictive per-finding disposition (BLOCK > REDACT > FLAG > ALLOW), to keep MONITOR non-enforcing, to keep BLOCK terminal and to route UNAVAILABLE through the owning rule's `on_unavailable` (§10.5.3, C22, GW07).

3.5 WHEN a plan contains only `scope: both` rules THEN input and output decisions SHALL CONTINUE TO equal today's decisions for the same findings.

**Detection**

3.6 WHEN a text produced a finding before the fix (email, phone, SSN, AWS key, API key, PEM header, injection override, role reassignment, jailbreak phrase, command construct) THEN the system SHALL CONTINUE TO emit that finding with the same category and original-byte span.

3.7 WHEN a benign prompt contains inline backtick code or developer text (LGW09-1) THEN the system SHALL CONTINUE TO emit no command finding and make no terminal block.

3.8 WHEN canonicalisation's decode budget is exceeded THEN the system SHALL CONTINUE TO reject explicitly with an UNAVAILABLE finding, never by silent truncation (GW09, C26). WHEN text contains no confusable or encoded content THEN the canonical form SHALL CONTINUE TO equal the input with an identity span map.

3.9 WHEN detection runs THEN it SHALL CONTINUE TO use one canonicalisation pass and one multi-pattern match per channel value, with no per-pattern loop over the text (GW09).

**Admission, budget and lifecycle**

3.10 WHEN admission sheds THEN the system SHALL CONTINUE TO return the declared `ShedVerdict` with a jittered Retry-After of at least 1 s and `x-should-retry: false`, and SHALL CONTINUE TO run the SIGTERM drain state machine without truncating declared terminations (GW19).

3.11 WHEN the budget lease is exhausted or the store is unavailable THEN the system SHALL CONTINUE TO spend the remaining lease locally, then refuse only quota with the narrow `budget_unavailable` (never `shared_state_unavailable`), with no store I/O on the request path, async refills at the low watermark and no multiplication of the org budget by worker or replica count (GW06, R2-09).

3.12 WHEN a request is refused before identity resolution THEN the system SHALL CONTINUE TO render the same declared codes (`request_pre_check_exceeded`, parse codes, `shared_state_unavailable`, `plan_unavailable`) with `request_id: null`. Refusals after identity SHALL CONTINUE TO echo the gateway-minted `zs-` request id.

3.13 WHEN the gateway boots without the dev profile THEN the default boot path SHALL CONTINUE TO be fail-closed: `fail_closed_resolver` identity, the shipped minimal output resolver and every request refusing with its declared code, with no change to its wiring or boot log beyond what the phase-aware resolver and lifecycle completion require.

**Build and test gates**

3.14 WHEN the change is verified THEN the import-linter layer contract (edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain, `egress` never importing `detect`), the capacity-literal gate (no capacity literal outside `runtime/resources.py`), all seven AST gates, `mypy --strict` and ruff SHALL CONTINUE TO pass.

3.15 WHEN the full offline suite runs THEN it SHALL CONTINUE TO pass from the baseline of 1948 passed / 95 skipped / 2 xfailed, with exactly one fewer xfail (the flipped input-REDACT test now passing), no new failures, and every other recorded xfail (including the R2-04 `store_ahead` loop) unchanged. New property tests SHALL follow the house idiom: seeded `random.Random`, at least 10,000 iterations, no hypothesis.

**Enforcement outcomes (embeddings surface)**

3.16 WHEN a `/v1/embeddings` input has no findings, or only FLAG or MONITOR findings, THEN the system SHALL CONTINUE TO forward every input item byte-identical to the parsed request, with the same shape and order. WHEN any embeddings input finding resolves to BLOCK THEN the system SHALL CONTINUE TO refuse with `input_blocked` before the provider is opened, with zero provider calls.
