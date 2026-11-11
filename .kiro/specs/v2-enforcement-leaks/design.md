# v2 Enforcement Leaks (Phase 1) Bugfix Design

## Overview

Six defects in `gateway_v2` let a tenant-selected policy be silently defeated or leak a bounded resource. They were found by running the opt-in dev profile (`AMF_DEV_PROFILE=1`, `dev/plan.example.json`). Every root cause below was confirmed by reading the code and, where noted, by a live repro against the unfixed tree. The runbook `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` is the authority. Where the runbook leaves a point open, gateway v1 parity decides it, and each such decision names the v1 code it follows.

| Group | Defect | Fix in one line |
|---|---|---|
| A | Input REDACT is decided, then thrown away. Raw bytes go to the provider, on chat and on `/v1/embeddings`. | Keep the input `Decision`, mint the GW11 authorization from it, and build the provider body only from `apply_and_verify` output. Embeddings items are deltas on the same path. Fail closed with `input_unmaskable` (HTTP 400) on any verification failure. |
| B | Card numbers never fire, because the leftmost-first matcher lets the `pii_phone` prefilter consume the digit run. No Luhn check. | Run a bounded rescan of every failed-confirm region, add a declared `luhn` validator, and bound every prefilter's length. |
| C | `you are DAN` (plus two sibling jailbreak phrases) is missed, and Greek and most Cyrillic confusables are not folded. | Make each prefilter a start-superset of its precise regex. Move to one versioned confusable table whose version feeds `detector_version`. |
| D | Admission slots are never released. `complete` pops the wrong slot and never prunes. | Use a per-request hold that calls `complete` exactly once in a `finally`. Make `complete` idempotent, slot-exact and pruning. |
| E | One resolver bound to one phase serves both phases. | Add a per-request, per-phase resolver factory, `resolve(findings, plan, Phase.INPUT \| Phase.OUTPUT)`. |
| F | The lease chunk is counted in requests while the cost is counted in characters. A refused request keeps its charge. | Use tokens end to end, with v1's `estimate_prompt_tokens` as the request cost over the v1-clamped output budget. The chunk is at most remaining / active workers and is contract-capped. Allow a bounded overdraft and refund when nothing is dispatched. |

The default fail-closed boot path is unchanged. The new `ChatRoute`, `MiscRoute`, `CanonicalRequest` and `MiscEmbeddingsRequest` fields default to today's behaviour, and the default boot sheds at admission, before any changed code runs. The six fixes are made bottom-up by layer (see "Order of changes").

## Glossary

- **Bug_Condition (C)**: the predicate over a scenario `X` that selects inputs on which the unfixed gateway misbehaves. It is a disjunction `C_A ∨ C_B ∨ C_C ∨ C_D ∨ C_E ∨ C_F`.
- **Property (P)**: the required outcome for every `X` with `C(X)`.
- **Preservation**: for every `X` with `¬C(X)`, the fixed gateway `F'` behaves exactly like the original `F`. Detection is *monotone*: `F'` emits every finding `F` emitted, possibly with more.
- **F / F'**: the original and the fixed code.
- **Scenario X**: a tagged record `(surface, plan, request, detector_text, admission_events, budget_events)`. Each sub-condition reads only its own fields.
- **Request content coordinate space**: the concatenation of `GatewayRequest.text_deltas` texts in order, as defined by `dispatch/verify.py`. C21 requires spans that are "addressable across the whole request, not per string".
- **Channel base offset**: the start of delta *i* in the request content coordinate space.
- **Embeddings item delta**: one `("embeddings_input", item)` pair per `/v1/embeddings` input item, in request order. A string input is one item. Embeddings requests use the same coordinate space as chat, with each item as one delta.
- **Primary pass**: the shipped leftmost-first `_COMBINED.finditer` over canonical text, in `detect/deterministic/matcher.py::scan`.
- **Failed-confirm region**: `[hit.start, hit.end)` of a primary prefilter hit whose precise confirm (or validator) failed.
- **Start-superset prefilter**: a prefilter that matches at every position where its precise regex can begin a match.
- **Confusable set**: the declared, versioned table of Cyrillic and Greek letters folded to a Latin look-alike during canonicalisation.
- **Request hold**: a per-request, edge-local, one-shot record of "admitted as `request_id`", "spent `cost` tokens" and "provider opened". It is released exactly once.
- **Token estimate**: `ResourceContract.estimate_request_tokens(chars, max_tokens, n) = max(1, input + max_tokens × n)`, where `input = max(1, chars // D)` when `chars > 0` and `0` otherwise. This is v1's `LLMRouter.estimate_prompt_tokens` (`gateway/ai_mesh_gateway/llm_router.py`). `D` is `_CHARS_PER_TOKEN_DIVISOR = 4`, declared in `runtime/resources.py`.
- **Output budget clamp**: v1's pre-estimate clamp (`gateway/ai_mesh_gateway/main.py`, chat path). `n` becomes `min(n, 1)`, because v1 forces `n = 1` at the boundary for its single-choice output guard. `max_tokens` becomes `min(max_tokens, org response-token ceiling, 65536 hard cap)` when it is above 0. Both run before the cost estimate. Each change is recorded as a signalled mutation (v1 I-20, `_request_clamps`).
- **Output-token boundary**: `1_000_000`, v1's `MAX_OUTPUT_TOKENS_CEILING`. A `max_tokens` or `max_completion_tokens` above it is refused at parse with HTTP 400. Values at or below it are clamped, not refused.
- **Prompt chars**: the character count fed to the token estimate. For chat it is the summed length of every folded input text channel, with the content parts of one message joined by a single space, as v1 joins list content. For embeddings it is the summed length of the input items.
- **Overdraft**: a spend of `cost > Remaining_Lease` admitted while the lease still holds budget. The lease goes negative by at most `contract.max_request_tokens() − 1` and the next refill settles the debt.
- **`input_unmaskable`**: the new declared refusal code for an input REDACT whose transformed bytes cannot be verified. It is rendered through `edge/errors` as HTTP 400 `invalid_request_error`, the same family as `input_blocked`.

## Bug Details

### Bug Condition

```
FUNCTION isBugCondition(X)
  INPUT: X : Scenario
  OUTPUT: boolean
  RETURN C_A(X) OR C_B(X) OR C_C(X) OR C_D(X) OR C_E(X) OR C_F(X)
END FUNCTION

// A — an input REDACT that is not applied before the provider (chat or embeddings)
FUNCTION C_A(X)
  IF X.surface = CHAT THEN
    F_in := input_findings(X.request)               // every input channel, plus joined parts
  ELSE IF X.surface = EMBEDDINGS THEN
    F_in := UNION over i of shift(findings(X.request.input_items[i]), base_offset(i))
  ELSE
    RETURN false
  END IF
  D := resolve(F_in, X.plan, Phase.INPUT)
  RETURN D.disposition = REDACT                     // implies no BLOCK (BLOCK > REDACT)
         AND X.request reaches the proceed outcome   // admitted, budget-admitted
END FUNCTION

// B — a confirmable match lost to an earlier failed prefilter, or a card without Luhn
FUNCTION C_B(X)
  c := canonicalize(X.detector_text)
  RETURN EXISTS pattern P, region R:
           R is a failed-confirm region of some pattern Q ≠ P in the primary pass
           AND P (prefilter, precise and validator) confirms a match m with m ∩ R ≠ ∅
           AND m is not emitted by scan_F(c)
      OR EXISTS digit run d in c: card_shaped(d) AND NOT luhn_valid(d)  // the validator is missing
END FUNCTION

// C — a precise phrase that starts before its prefilter keyword, or a confusable outside the table
FUNCTION C_C(X)
  c := canonicalize_F(X.detector_text)
  RETURN EXISTS P, precise match m of P in c: prefilter_P does not match at m.start
      OR EXISTS ch in X.detector_text: ch IN DeclaredConfusables AND ch NOT IN _HOMOGLYPHS_F
END FUNCTION

// D — an admitted request exits (any path), or complete() receives an unknown or repeated id
FUNCTION C_D(X)
  RETURN EXISTS e in X.admission_events:
           e = Admitted(request_id)                 // every admitted request eventually exits
      OR EXISTS e: e = complete(id) AND id NOT IN unfinished_admitted_ids
END FUNCTION

// E — the plan contains rules not scoped BOTH
FUNCTION C_E(X)
  RETURN EXISTS r IN X.plan.rules: r.scope IN {INPUT, OUTPUT}
END FUNCTION

// F — the request reaches the budget phase (its cost changes unit), the chunk ignores the
// remaining budget, a spend is kept with zero provider calls, or the request carries an
// output-budget field the fixed parser refuses (F ignores max_tokens, max_completion_tokens and n)
FUNCTION C_F(X)
  RETURN X.request reaches the budget phase          // cost_F = chars, cost_F' = v1 token estimate
      OR (budget_spent(X) AND provider_calls(X) = 0)
      OR X.lease_acquire_happens                    // the chunk ignores remaining / active workers
      OR output_budget_off_contract(X.request)      // non-int, negative, n < 1, or > 1_000_000
END FUNCTION
```

`token_cost(X.request) > chunk_F(X.contract)`, the case the bug report names, is a sub-case of the first disjunct of `C_F`.

### Examples (observed on the unfixed tree)

- **A, chat.** The dev plan with `"My SSN is 123-45-6789 thanks"`, non-streaming: the provider receives `123-45-6789`. This is the strict xfail `test_input_redact_masks_pii_before_the_provider`. Expected: the provider receives `My SSN is [REDACTED] thanks`.
- **A, embeddings list.** The dev plan with `input: ["release notes", "My SSN is 123-45-6789"]`: `EmbeddingsSurface._input_scan` resolves REDACT, returns `None` (not BLOCK), and `_input_as_deltas` forwards both items raw. Expected: the provider receives `("embeddings_input", "release notes")` byte-identical and `("embeddings_input", "My SSN is [REDACTED]")`, two deltas in that order.
- **A, embeddings string.** `input: "mail jane@example.com"` is forwarded raw today. Expected: exactly one delta, `"mail [REDACTED]"`.
- **A (latent, reached once A is wired).** In `dispatch/verify.py::_resplit`, redacting `"SSN 123-45-6789"` in delta 0 shortens that delta. The re-split then keeps delta 0's *original* length and moves one byte of delta 1 into delta 0. Expected: each delta keeps exactly its own sanitized bytes. For embeddings this would move bytes between independent input items.
- **A (latent).** `_redacting_detectors` keys obligations by detector id. A plan with `SECRET → flag` and `CREDENTIAL → redact` makes the uncovered SECRET span of `deterministic.secret` "obliged", and the request fails closed with no leak. Expected: obligations are keyed per finding.
- **B.** Live repro, `scan()` on canonical text: for `4111-1111-1111-1111`, `4111111111111111` and `4111 1111 1111 1111` the only primary hit is `pii_phone` (whole run), the phone precise fails, and the result is `[]`. Expected: one `credential_card` CREDENTIAL finding over the whole PAN. `4111-1111-1111-1112` (fails Luhn) must emit no card.
- **C.** Live repro: `you are DAN` and `you are DАN` (Cyrillic А) both give the hit `injection_jailbreak@8..11`, the confirm fails, and the result is `[]`. The same failure affects `enable developer mode` (hit at offset 7) and `answer without any restrictions` (hit at offset 7). `Ιgnore all previous instructions` (Greek Ι U+0399) gives no hit at all.
- **D.** The GW15 lifecycle admits and never calls `complete`. After `request_depth` admitted requests every request sheds `overload_shed`. The dev profile hides this with the `DevServedApp` ASGI wrapper. `complete("unknown")` pops another request's slot.
- **E.** A dev plan rule with `"scope": "input"` gives the boot refusal `DevProfileError("... accepts only scope 'both' rules")`. In a programmatic wiring, an input-only REDACT rule is applied to output (resolver bound to `Phase.INPUT`).
- **F.** The contract-derived chunk is `min(ceil(q_safe·p99), queue_depth)`, a request count such as 10. The lifecycle charges `len(text)` characters, so a 2,000-character prompt can never be admitted. The GW15 parser reads neither `max_tokens` nor `n`, so a request for `n=8` completions of `max_tokens=4096` is charged as if it asked for none. Nothing bounds the client-chosen output budget either: `max_tokens=2000000` is accepted. A request that is input-BLOCKed after the budget phase keeps its charge. Expected for the 2,000-character prompt with `max_tokens=256, n=2`: `n` is clamped to 1 (v1 parity), so the cost is `2000 // 4 + 256 × 1 = 756` tokens, admitted while the org budget holds it. Expected for `max_tokens=100000, n=8`: clamped to `max_tokens=4096, n=1` and charged `input + 4096`. Expected for `max_tokens=2000000`: HTTP 400 `invalid_max_tokens` with `param` `max_tokens`, before admission.

## Expected Behavior

### Preservation Requirements

**Unchanged behaviours:**
- Input BLOCK, including BLOCK co-occurring with REDACT or FLAG, refuses `input_blocked` before the provider with zero provider calls (3.1).
- ALLOW, FLAG and MONITOR-only inputs reach the provider byte-identical to the parsed request: `apply_and_verify` with no transformations returns the same deltas (3.2).
- The output guard masks redactable reply values, streaming and non-streaming (3.3).
- The resolver stays pure, per-finding (C22) and most-restrictive. MONITOR stays non-enforcing, BLOCK stays terminal, and UNAVAILABLE routes through `on_unavailable` (3.4). For `scope: both` plans, input and output decisions are unchanged (3.5).
- Every finding the original matcher emitted is still emitted with the same category and span (3.6). Benign inline backticks still produce no command finding (3.7). The decode budget rejects explicitly, and confusable-free text canonicalises to itself with an identity span map (3.8). There is still one canonicalisation pass and one multi-pattern match per channel value (3.9).
- Shed verdicts, Retry-After ≥ 1 s, `x-should-retry: false` and the drain state machine are unchanged (3.10). Lease outage semantics, `budget_unavailable` (never `shared_state_unavailable`), no request-path store I/O, async refills and no worker multiplication are unchanged (3.11). Pre- and post-identity refusal rendering and `request_id` echo are unchanged (3.12). The default boot table and its log are unchanged except for the phase/lifecycle lines (3.13).
- All gates stay green: the import-linter layers (`egress` never imports `detect`), the seven AST gates, `mypy --strict` and ruff (3.14). The full suite passes with exactly one fewer xfail (3.15).
- `/v1/embeddings` inputs with no findings, or only FLAG or MONITOR findings, reach the provider item-for-item byte-identical, in the same shape and order. An embeddings BLOCK still refuses `input_blocked` with zero provider calls (3.16).

**Scope.** Every input outside `C(X)` is unaffected. That covers:
- requests with no input findings, or only FLAG or MONITOR findings, on chat and embeddings
- texts whose matches are all confirmed by the primary pass and contain no confusable outside the original table
- plans whose rules are all `scope: both`
- requests refused before admission, except those whose output-budget fields the fixed parser refuses (in `C_F`)
- the provider bytes, output bytes and refusal codes of any request whose token cost the lease covers (only the ledger unit changes for those)

## Hypothesized Root Cause

Each cause was confirmed by reading the code, and B and C by live repro as well.

1. **A: decision discarded.** `edge/openai_chat.py::ChatSurface._input_scan` resolves the findings, returns only a refuse code, and refuses only on BLOCK (its docstring calls REDACT "out of this phase's scope"). `_proceed` builds the body from the raw `req.text_deltas`. `edge/openai_misc.py::EmbeddingsSurface` has the same shape: `_input_scan` returns `block_code` only when `decision.disposition is Disposition.BLOCK`, otherwise `None`, and `_collect_and_render` packs `_input_as_deltas(req)`, the raw parsed items. GW11's `dispatch/verify.apply_and_verify` and `resolve/authorize.authorize` exist but have no caller on any edge path. Three latent defects become reachable once this is wired:
   - per-channel (and per-item) findings are pooled with channel-local spans, not request-content spans
   - `_resplit` keeps the original per-delta lengths
   - `_spans_to_verify` keys obligations by detector id
2. **B: leftmost-first consumption.** `matcher.scan` uses a single `finditer` over one ordered alternation. At any position the first matching branch wins and the cursor jumps to its end. `pii_phone` precedes `credential_card` in `RULESET` and its prefilter `(?:\+?\d[\s().\-]?){9,}\d` covers every card shape. Its precise regex rejects the run, and no other branch is ever tried there. `credential_card` has no checksum.
3. **C: precise anchored at the prefilter hit.** `precise.match(text, hit.start)` requires the precise regex to start where the prefilter matched. The jailbreak prefilter alternatives `\bDAN\b`, `developer\s+mode` and `without\s+…restrictions` start *after* the precise starts (`you are`, `enable`, `answer`). `_HOMOGLYPHS` has 20 entries and lacks the Greek Ι, Β, Ζ, Η, Κ, Μ, Ν, Ρ, Τ, Υ, Χ and several Cyrillic look-alikes. A second, divergent table lives in `detect/semantic/language.py::CONFUSABLE_FOLD`. `ruleset_version()` hashes patterns only, so a fold change would not move `detector_version`.
4. **D: no completion site.** Neither `ChatSurface.run_lifecycle` nor `ChatRoute.handle` (nor `EmbeddingsSurface`) calls `complete`. In `AdmissionController.complete`, `queue.pop()` removes the FIFO head, not the completed request's slot, and runs for unknown ids. In-flight records are flagged finished and never deleted. The request id is a content hash (`ChatSurface._request_id`, `EmbeddingsSurface._request_id`), so two concurrent identical requests share an id. `complete` must tolerate that without leaking or double-releasing.
5. **E: phase-blind seam.** `ChatRoute.resolver: OutputResolver` and `MiscRoute.resolver` expose `decide(matches)` with no phase. The dev profile binds `PlanResolver(plan, Phase.INPUT)` once at boot and refuses non-`both` plans to stay honest.
6. **F: unit mismatch.** `admit/quota.py::derive_lease_chunk` returns a request count (`min(ceil(q_safe·p99/1000), queue_depth)`). `ChatSurface._estimated_cost` and `EmbeddingsSurface._estimated_cost` return characters. `OpenAIRequestParser.parse` never reads `max_tokens` or `n` (`CanonicalRequest` has no such fields), so the requested output budget is not charged at all, and nothing bounds it. `BudgetLease.try_spend` refuses any `cost > remaining`. The acquire grants `min(pool, chunk)` with no fair-share term. There is no refund API.

## Correctness Properties

Property 1: Bug Condition - Enforcement leaks closed

_For any_ scenario X where isBugCondition(X) holds, the fixed gateway SHALL:
- (A) send the provider only the verified, transformed request bytes, on chat and on `/v1/embeddings`, with every REDACT span replaced by `[REDACTED]`, or refuse with a declared code (`input_unmaskable` renders HTTP 400 `invalid_request_error`) and zero provider calls
- (B/C) emit every confirmable, validator-passing match whose region was not consumed by a confirmed match, including Luhn-valid PANs and confusable- or lead-context-spelled jailbreak and injection phrases, with original-byte spans whose redaction removes the planted bytes
- (D) call `complete` exactly once per admitted request and release exactly that request's slot
- (E) resolve input findings at `Phase.INPUT` and output findings at `Phase.OUTPUT` against the request's plan
- (F) charge and lease in tokens using the v1 token estimate over the v1-clamped output budget, size each grant at most remaining / active workers within the contract cap, admit a cost larger than a chunk while budget remains, and refund a spent cost when nothing was dispatched

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19, 2.20, 2.21**

Property 2: Preservation - Non-buggy behaviour unchanged

_For any_ scenario X where isBugCondition(X) does NOT hold, the fixed gateway SHALL produce the same result as the original: the same disposition, refusal code, HTTP status and `request_id` echo; the same provider bytes (chat deltas and embeddings items); the same output bytes; the same admission, shed and drain outcomes; the same lease ledger. Detection SHALL be monotone: `findings_F(t) ⊆ findings_F'(t)` on `(category, span)` for every text `t`, with equality whenever `C_B ∨ C_C` is false for `t`.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.16**

Property 3: Bug Condition (A) - Input REDACT masked in every channel

_For any_ chat request (streaming or not) whose input decision is REDACT, the provider body's `text_deltas` SHALL equal `apply_and_verify(decision, request).request.text_deltas`. No REDACT finding's original bytes SHALL appear in any delta. Each delta SHALL hold exactly its own sanitized bytes, including system messages, later messages, multi-part content and values split across consecutive parts of one message.

**Validates: Requirements 2.1, 2.3**

Property 4: Bug Condition (A) - Unverifiable redaction fails closed

_For any_ REDACT decision, on chat or on `/v1/embeddings`, whose transformations are a no-op, out of range, partial or overlapping, or whose verified upstream does not keep one delta per embeddings item, the lifecycle SHALL refuse `input_unmaskable` through `edge/errors` with HTTP 400 and `error.type` `invalid_request_error`, make zero provider calls and refund the spent budget.

**Validates: Requirements 2.2, 2.17, 2.21**

Property 5: Bug Condition (B) - Cards detected exactly when Luhn-valid

_For any_ 13–19-digit run with no separators, single spaces or single dashes, embedded in arbitrary text, the scan SHALL emit a `credential_card` CREDENTIAL finding covering the whole PAN iff the digits pass Luhn and the length is in range.

**Validates: Requirements 2.4, 2.5**

Property 6: Bug Condition (B) - Order independence over failed regions

_For any_ text, every match a single pattern P would confirm *alone* (single-pattern reference scan) SHALL either appear in `scan(text)` or lie inside the span of a match of another pattern that `scan(text)` confirmed. The fixed scan SHALL remain one primary `finditer` plus region rescans whose total length is at most the summed failed-region lengths.

**Validates: Requirements 2.6**

Property 7: Bug Condition (C) - Phrase and confusable coverage

_For any_ spelling of `you are DAN`, `enable developer mode`, `answer without (any) restrictions` or an override phrase, in which any subset of letters is replaced by a declared confusable, the injection detector SHALL emit the expected EXECUTED finding. Redacting its span in the original text SHALL remove every confusable-bearing byte. Every pattern's prefilter SHALL be a start-superset of its precise regex.

**Validates: Requirements 2.7, 2.8**

Property 8: Bug Condition (C) - Versioned confusable set

_For any_ change to the confusable table, `detector_version` SHALL change. For any character in the table, `canonicalize` SHALL fold it to its declared Latin letter with a 1:1 span record.

**Validates: Requirements 2.8**

Property 9: Bug Condition (D) - Exactly-once completion

_For any_ sequence of chat and embeddings requests whose exits are drawn from success, `[DONE]`, error frame, budget refusal, input BLOCK, scan failure, `input_unmaskable`, output BLOCK, raised exception, cancellation, kill-switch cut and stream timeout, each admitted request SHALL be completed exactly once. No request refused before admission SHALL be completed. Request-queue depth SHALL equal the number of admitted, unfinished requests after every step.

**Validates: Requirements 2.9, 2.10, 2.12**

Property 10: Bug Condition (D) - Idempotent, exact, pruning `complete`

_For any_ interleaving of `admit` and `complete` calls, including unknown, repeated and colliding ids, `complete` SHALL release at most one slot per prior unmatched admit of that id and SHALL release none otherwise. `len(_in_flight)` SHALL be at most the number of admitted, unfinished ids.

**Validates: Requirements 2.11**

Property 11: Bug Condition (E) - Phase-scoped resolution

_For any_ plan and findings, the input decision (chat and embeddings) SHALL equal `resolve(findings, plan, Phase.INPUT)` and every output decision (streaming, non-streaming, error frame) SHALL equal `resolve(findings, plan, Phase.OUTPUT)`. The dev profile SHALL boot every valid compiled plan.

**Validates: Requirements 2.13, 2.14**

Property 12: Bug Condition (F) - Token lease conservation

_For any_ multi-worker spend, refund and return sequence over one pool:
- every grant SHALL be at most `min(pool, cap, max(1, pool // active_workers))`
- admitted tokens minus refunds SHALL be at most `limit + Σ_workers overdraft_bound`
- no cost at most `contract.max_request_tokens()` SHALL be refused while the worker's lease holds budget, and a cost the lease cannot cover within the overdraft bound SHALL be refused `budget_unavailable` with nothing spent
- a refund SHALL restore exactly the refunded cost to the lease of the same generation
- every surface and the dev profile SHALL charge `ResourceContract.estimate_request_tokens(prompt_chars, max_tokens, n)` over the clamped `max_tokens` and `n`. That value SHALL equal a frozen copy of v1's `estimate_prompt_tokens` applied after v1's clamp on chat requests whose only text is message content, and SHALL be at least that value otherwise

**Validates: Requirements 2.15, 2.16, 2.17, 2.18**

Property 13: Preservation - Detection monotone against the frozen original

_For any_ text, the fixed scan's `(pattern, span)` set SHALL be a superset of a frozen copy of the original matcher and ruleset run over the original canonical text, and equal to it whenever the text has no failed-confirm region, no lead-context phrase and no newly-declared confusable.

**Validates: Requirements 3.6, 3.7, 3.8, 3.9**

Property 14: Preservation - Enforcement and lifecycle unchanged off the bug condition

_For any_ chat or embeddings request whose input decision is ALLOW, FLAG or BLOCK under a `scope: both` plan, whose token cost the lease covers, and with no exit-path completion observed, the fixed lifecycle SHALL produce the same refusal code, HTTP status, provider bytes and output bytes as the original. Embeddings items SHALL reach the provider byte-identical, in the same count and order. The default boot SHALL render the same declared codes.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.10, 3.11, 3.12, 3.13, 3.16**

Property 15: Bug Condition (A, embeddings) - Embeddings input REDACT masked per item, shape preserved

_For any_ `/v1/embeddings` request (string input or list input of any length) whose input decision is REDACT, the provider body SHALL hold exactly one `embeddings_input` delta per input item, in request order. A string input SHALL yield exactly one delta. Every item with a REDACT finding SHALL have each finding's original bytes replaced by `[REDACTED]`, with no raw byte of that finding in any delta. Every item with no redactable finding SHALL be byte-identical to the parsed item. No item's bytes SHALL appear in another item's delta.

**Validates: Requirements 1.18, 2.19, 2.20**

Property 16: Bug Condition (F) - Output budget clamped and bounded (v1 parity)

_For any_ chat request whose `max_tokens`, `max_completion_tokens` and `n` are drawn from absent, JSON `null`, `0`, in-range, exactly `1_000_000`, above `1_000_000`, and off-contract values (bool, float, string, negative, `n = 0`), and for any org ceiling of at least 1:
- JSON `null` SHALL parse identically to an absent field
- a `max_tokens` above `1_000_000` SHALL refuse `invalid_max_tokens`, and a `max_completion_tokens` above it SHALL refuse `invalid_max_completion_tokens`, each as HTTP 400 `invalid_request_error` with `param` naming the field and `request_id` null. The request SHALL NOT be admitted, SHALL make zero provider calls and SHALL spend no budget
- an off-contract value SHALL refuse `MALFORMED_WRONG_SHAPE`, as before this decision
- otherwise the clamped request SHALL satisfy `max_tokens = min(parsed, org_ceiling, ResourceContract.max_output_tokens())` and `n = min(parsed, ResourceContract.max_completions())`, so `max_tokens ≤ 65536` and `n = 1`
- the clamp SHALL be idempotent: clamping a clamped request returns an equal request, `request_clamps` included
- `request_clamps` SHALL hold exactly one entry per changed field, in v1's `"<field>=<pre>-><post>"` form, and none for an unchanged field
- the budget phase SHALL charge `estimate_request_tokens(prompt_chars, clamped max_tokens, clamped n)`, which SHALL be at most `contract.max_request_tokens()`

**Validates: Requirements 2.15, 2.16, 2.18**

## Fix Implementation

### Changes Required

Module sizes are a constraint: `edge/openai_chat.py` is at 800/800 lines and `edge/openai_misc.py` at 799/800 under `check_sizes`. New edge logic therefore goes into new sibling modules, and each touched surface module must end at or below its current line count.

#### Group A — Input REDACT through the authorized dispatch

**`domain/decision.py`**
- Add `FindingDisposition.finding_index: int | None = None`. It is the index into `Decision.findings`, defaulted so every existing construction compiles.

**`resolve/resolver.py`** (pure, imports only domain)
- Set `finding_index` for every per-finding disposition.
- Normalise REDACT transformations. Sort by span, then coalesce overlapping or identical spans that share a replacement into their union. Spans that only touch (`a.end == b.start`) are never merged, so spans from two embeddings items or two chat deltas never fuse across a delta boundary. This is needed because B/C can now emit overlapping findings from different patterns, and the edge scans single parts plus joined parts. For non-overlapping input the output is identical (preservation). Covered by the resolver property tests.

**`dispatch/verify.py`**
- `_spans_to_verify`: when every `per_finding` carries a `finding_index`, oblige exactly those findings' spans. Otherwise fall back to the shipped detector-keyed rule (e.g. `MinimalOutputResolver`).
- `_resplit(sanitized, request, transformations)`: compute each delta's new boundary by shifting the original boundary by the summed length delta of the transformations that end at or before it. A transformation that straddles a boundary (a value split across parts) places its replacement in the delta where it starts, and the later delta loses the covered prefix. Raw bytes never cross channels, and each delta holds only its own sanitized bytes. Embeddings findings never straddle a boundary (each item is scanned alone), so every embeddings delta is transformed independently.

**`dispatch/authorized.py`**
- Extract steps (1) `assert_authorized`, (2) `apply_and_verify` and (4) `Transform.to_provider` of `AuthorizedProviderEntry.dispatch` into a public `verified_upstream(auth, *, decision, request, transform) -> UpstreamRequest`.
- `dispatch()` calls it, so there is one code path. `VerificationFailure` propagates.

**`edge/input_enforcement.py`** (new; edge is above detect/resolve/dispatch)

This is the one input-redaction path. Chat and embeddings both call it.
- `scan_deltas(text_deltas, *, join_runs, detector, resolver, on_invoke=None) -> InputScan`:
  - Run the detector once per delta, calling `on_invoke()` per run when given (embeddings uses it for `exports.observe_detector_invocation`).
  - Also run it once over the joined text of each `(first, last)` delta run in `join_runs`. Chat passes the maximal runs of consecutive `content_part_text` deltas of the same message (C21 "the joined text of each message's parts"). Embeddings passes `()`, because items are independent inputs.
  - Shift every finding's spans by the channel base offset, so findings live in request content coordinates.
  - Drop exact duplicates, then resolve once.
  - Returns `InputScan(decision, findings)`. A detector or resolver raise maps to `scan_failure` (unchanged).
- `scan_input(req, detector, resolver) -> InputScan`: the chat wrapper. It derives `join_runs` from `req.input_message_index` and calls `scan_deltas(req.text_deltas, ...)`.
- `stage_upstream(decision, request: GatewayRequest, transform) -> UpstreamRequest | refuse_code`:
  - `auth = authorize(decision)`. `None` means BLOCK and gives `input_blocked`, which is defensive because BLOCK is refused earlier.
  - Then `verified_upstream(auth, decision=..., request=request, transform=...)`.
  - `VerificationFailure` gives `REFUSE_INPUT_UNMASKABLE`. The sealed `DispatchAuthorization` is only minted in `resolve/` and is only checked here, so the provenance gate is untouched.

**`edge/wire/openai.py`**
- `fold_input_channels` also returns the message index of each delta.
- `CanonicalRequest` gains `input_message_index: tuple[int, ...] = ()`, parallel to `input_channels`. The fold stays total (`_assert_fold_total` unchanged).

**`edge/openai_chat.py`**
- `_input_scan` delegates to `scan_input` and returns `(refuse_code, decision)`.
- `_proceed(plan, req, decision, request_id)` uses `stage_upstream(decision, GatewayRequest(destination, req.model, req.text_deltas), transform)`. The proceed body is now always the verified upstream body: identical deltas for ALLOW/FLAG, masked deltas for REDACT.
- Streaming and non-streaming share `_proceed`, so both modes are covered.
- `REFUSE_INPUT_UNMASKABLE = "input_unmaskable"`.

**`edge/wire/openai_misc.py`**
- `MiscEmbeddingsRequest` gains `input_is_string: bool = False`. `parse_embeddings` sets it when the JSON `input` is a string. The field is pure, defaulted so every existing construction compiles, and lets the stage step and the tests check the shape clause (2.20) directly. Rendering a string versus a list on a real provider wire is the Phase 2 HTTP client's job; the stub provider and `Transform` consume deltas, which already carry one item per delta.

**`edge/embeddings_input.py`** (new; edge sibling of `openai_misc.py`, keeps that module under the size gate)
- `embeddings_deltas(req) -> tuple[tuple[str, str], ...]`: one `("embeddings_input", item)` per item, in order. This moves `EmbeddingsSurface._input_as_deltas` out of `openai_misc.py`.
- `scan_embeddings(req, *, detector, resolver, on_invoke) -> InputScan`: calls `scan_deltas(embeddings_deltas(req), join_runs=(), ...)`. Each item's findings are shifted by its base offset, so they live in the request content coordinate space like chat findings.
- `stage_embeddings(decision, req, *, destination, transform) -> UpstreamRequest | refuse_code`:
  - Calls `stage_upstream(decision, GatewayRequest(destination, req.model, embeddings_deltas(req)), transform)`. This reuses the chat path exactly: `resolve.authorize` → `dispatch/authorized.py::verified_upstream` / `apply_and_verify`. There is no second redactor.
  - After a successful stage it checks the shape: the upstream deltas count equals `len(req.input_items)`, every delta channel is `embeddings_input`, and `input_is_string` implies exactly one delta. A mismatch returns `input_unmaskable` (fail closed). With `_resplit` keeping each delta's own bytes, clean items come back byte-identical.
  - `VerificationFailure` (a detected value remains, or the transformation cannot be applied) returns `input_unmaskable`.

**`edge/openai_misc.py`** (`EmbeddingsSurface`)
- Delete `_input_scan` and `_input_as_deltas` (they move to `embeddings_input.py`). This frees about 45 lines.
- `handle` runs the input phase as `scan_embeddings(...)` with the `Phase.INPUT` resolver (Group E). BLOCK returns `input_blocked` and a raised scan returns `scan_failure`, both unchanged (3.16). Otherwise it calls `stage_embeddings(...)`. A refuse code renders through `_refuse` with `exports.record_withheld()`. A request with no REDACT finding goes through the same stage step with no transformations, so its items are forwarded byte-identical.
- `_collect_and_render(req, upstream, send, ...)` takes the staged `UpstreamRequest` instead of building the body from raw items. The provider is opened only with verified bytes.
- The holds pattern (Group D) refunds on `input_blocked`, `scan_failure` and `input_unmaskable`, because the provider was never opened.
- The module ends below its current 799 lines.

**`edge/errors.py`**
- Map `input_unmaskable` to `(400, "invalid_request_error")`. Place it directly after the existing `"input_blocked": (400, "invalid_request_error")` entry, under the same GW15/GW16 input-content-block comment. Extend that comment to say the GW01 frozen wire contract (§10.2.2) renders an input content refusal as HTTP 400 `invalid_request_error`, the stock SDK's `BadRequestError`, and that this matches gateway v1, where `_build_safe_block_response` re-maps a content-category block to `GATEWAY_BLOCK_STATUS` (default 400).
- Update the module's "Status choices" comment block: `output_blocked` and `scan_failure` stay 403; the input content refusals `input_blocked` and `input_unmaskable` are 400.

#### Group B — Matcher: failed-region rescan, Luhn, bounded prefilters

**`detect/deterministic/patterns.py`**
- Add `PatternSpec.validator: str | None` and set `credential_card.validator = "luhn"`.
- `ruleset_version()` serialises the validator name.
- Bound every unbounded prefilter. This is required so the rescan cannot become quadratic, and it removes a pre-existing CPU defect:

  | Pattern | Prefilter change |
  |---|---|
  | email local part | `{1,64}` |
  | email domain | `{1,253}` |
  | phone | `{9,14}` |
  | AWS key | `{10,16}` |
  | API-key value | `{12,512}` |

  Measured on the unfixed tree: `scan("x"*262_144)` takes **63.1 s** because of the unbounded email prefilter backtracking at every position. Card and SSN are already bounded.

**`detect/deterministic/matcher.py`**
- The primary pass is the shipped `_COMBINED.finditer`. The structure test `_COMBINED.groups == len(RULESET)` still holds.
- After a confirm, apply the pattern's validator from an immutable table (`MappingProxyType({"luhn": _luhn_ok})`).
- On a failed confirm or validator over `R = [hit.start, hit.end)`, run a region rescan. It evaluates every other pattern leftmost-first inside `R`, and the failed pattern at positions after `hit.start`. A nested failure recurses with the failed set excluded.
  - Single-exclusion combined regexes are precompiled at import into a `MappingProxyType`.
  - Deeper exclusion sets use a per-position all-lookahead regex `_ALL_AT`, compiled once, restricted to the nested region, which is at most the bounded prefilter length.
  - Confirms use `precise.match(text, q)` on the *full* text, never `endpos`, so `(?!\d)` at the region edge is not misread.
- Per-pattern cursors prevent duplicate or overlapping same-pattern emission across primary and rescan.
- Output is sorted by `(start, RULESET order)`.
- Cost: O(n·k·B), with B = maximum bounded prefilter length. Typical text with no failed confirms costs the same as today.

#### Group C — Start-superset prefilters, one versioned confusable set

**`detect/deterministic/patterns.py`**
- Add lead-context alternatives *before* the existing ones in the jailbreak prefilter: `you\s+are\s+DAN\b`, `enable\s+developer\s+mode`, `answer\s+without\s+(?:any\s+)?restrictions`. The existing alternatives stay, so it remains a superset.
- Audit every pattern for the start-superset invariant. The audit is backed by a generated-string test per precise alternative.
- The phone prefilter's start (no leading `(`) is deliberately left unchanged so existing spans are preserved. The precise confirm at the next digit already detects parenthesised numbers.

**`detect/confusables.py`** (new; detect top-level, beside `detect/windowing.py`)
- `CONFUSABLE_SET_VERSION = "amf-confusables-1"` and `CONFUSABLES: MappingProxyType[str, str]`.
- Selection rule: Cyrillic and Greek letters whose UTS #39 confusable prototype is a single ASCII letter, upper and lower case. Each entry is verified against the pinned `confusables.txt` when implemented.
- It is a superset of both current tables. It adds at least:
  - Greek Β Ζ Η Ι Κ Μ Ν Ρ Τ Υ Χ ι ν ρ υ
  - Cyrillic В К М Н Т У Ѕ І Ј Ӏ ѕ ԛ ԝ ӏ
- `CONFUSABLES_DIGEST` is a sha256 over the sorted items.

**`detect/deterministic/canonicalize.py`**
- `_fold_homoglyphs` reads `CONFUSABLES`. The fold stays 1:1 per character, so span records are unchanged (Property 2 span invariant).

**`detect/deterministic/patterns.py::ruleset_version`**
- Append one record `("confusables", CONFUSABLE_SET_VERSION, CONFUSABLES_DIGEST)`, so `detector_version` moves with the set (2.8).

**`detect/semantic/language.py`**
- `CONFUSABLE_FOLD = CONFUSABLES`, a re-export. This makes one table for GW09 and GW10 (C6 "confusable mapping"). It is a strict superset of the current GW10 table.

#### Group D — Exactly-once completion

**`admit/queues.py`**
- `BoundedQueue.remove(item) -> bool` removes one occurrence plus its aligned `_enqueued_at`. It is O(depth), and depth is contract-bounded.

**`admit/admission.py::complete`**
- Look up the id. If it is absent, return without touching any queue (2.11).
- Otherwise remove exactly that id's slot, decrement its admit count, and prune the in-flight record when the count reaches 0. This is a refcount, because content-hashed ids can legitimately collide across concurrent identical requests.
- Publish the queue.
- `_decide` increments the count on admit.
- `register_stream` and `finish_stream` (the B3 drain entrypoint) are unchanged.

**`edge/request_holds.py`** (new)
- `RequestHolds` is a plain `__slots__` class, per request and never shared. It records:
  - `admitted(request_id)`
  - `spent(cost, request_id)`
  - `mark_dispatched()`
  - `release(admission, quota)`, which runs once behind a latch. If the request was spent and not dispatched, it calls `quota.refund(cost, request_id=...)`. If it was admitted, it calls `admission.complete(request_id)`. Both are synchronous and local, so `release` never awaits inside `finally`.

**`edge/chat_deps.py`**
- `AdmissionDecider` gains `complete(request_id) -> None`.
- `BudgetDecider` gains `refund(cost, *, request_id) -> None`.
- The fail-closed defaults implement both as no-ops. The real `AdmissionController` and `QuotaComponent` satisfy them structurally.

**`edge/routes.py::ChatRoute.handle`**
- Create `holds`, then `try: run_lifecycle(scope, body, holds) …; finally: holds.release(self.admission, self.quota)`.
- This one `finally` covers every exit: refusal render, stream `[DONE]` or error frame, kill-switch and timeout cuts (which return normally through `_finish_stream`), exceptions and `CancelledError` on disconnect.
- `holds.mark_dispatched()` is called immediately before each of the two `provider.open` sites: `_next_attempt._attempt` and `openai_nonstream._collect_output_channels` (passed through).

**`edge/openai_misc.py`** (EmbeddingsSurface)
- Uses the same holds pattern: `holds.admitted` after an `Admitted` verdict, `holds.spent` after the budget phase, `holds.mark_dispatched()` immediately before `_drain_vectors` opens the provider, and `holds.release(...)` in a `finally` around the lifecycle. Embeddings also admits and never completes on a shared controller today. The handle changes fit because the input scan and delta packing move to `embeddings_input.py` (Group A).

**`edge/dev_admit.py`**
- Delete `DevCompletingAdmission`'s per-request bucket and `DevServedApp`'s per-request `begin/end`. The dev profile passes the bare `AdmissionController`.
- `DevServedApp` keeps only the lifespan lease startup and shutdown, which is not slot release.

#### Group E — Phase-aware resolution

**`edge/phase_resolvers.py`** (new)
- `PhaseResolverFactory = Callable[[ExecutionPlan, Phase], OutputResolver]`.
- `resolver_for(route, plan, phase)` returns `route.phase_resolvers(plan, phase)` when set, else `route.resolver`. The unset branch reproduces today's behaviour for every existing construction, including the default boot.

**`edge/routes.py`, `edge/openai_nonstream.py`, `edge/error_frame_scan.py` call sites, `edge/openai_misc.py`**
- Add a `ChatRoute.phase_resolvers` / `MiscRoute.phase_resolvers` field, typed `PhaseResolverFactory | None = None`.
- Input scan resolves with `Phase.INPUT`.
- `_build_pipeline(plan)`, `_ErrorFrameScan` and `_enforce_channels` resolve with `Phase.OUTPUT`, using the request's pinned plan.
- Embeddings input passes `resolver_for(deps, plan, Phase.INPUT)` to `scan_embeddings`. Moderations does not resolve and is unchanged.

**`edge/dev_components.py`**
- Delete `require_phase_agnostic`'s scope check, keeping the "no active rules" refusal.
- Replace `plan_resolver(plan)` with `phase_resolver_factory()`, which returns `lambda plan, phase: PlanResolver(plan=plan, phase=phase)`.

**`edge/dev_profile.py`**
- Wire `phase_resolvers` on both routes. Update the resolver boot line.

#### Group F — Token budget

**`runtime/resources.py`** (the only place capacity literals may appear)
- `_CHARS_PER_TOKEN_DIVISOR = 4`, the `D` of the token estimate. This is v1's divisor (`LLMRouter.estimate_prompt_tokens`), declared as a contract value. It replaces the earlier design's 3.04 GW10 markdown ratio, so the GW10 drift pin goes away.
- `ResourceContract.estimate_request_tokens(chars, max_tokens, n) -> int` is a `@staticmethod`, so a surface can call it without holding a contract instance (`MiscRoute` has none). It implements v1's formula exactly:
  ```
  input  := max(1, chars // D) IF chars > 0 ELSE 0
  RETURN max(input + max_tokens * n, 1)
  ```
  It raises `ValueError` on `chars < 0`, `max_tokens < 0` or `n < 1`. Callers pass parser-validated values, so the raise is a programming error, never a request path.
- Four declared output-budget values, all from gateway v1 (`gateway/ai_mesh_gateway/main.py`). Each is exposed through a `@staticmethod` on `ResourceContract`, so a surface can read it without a contract instance:

  | Constant | Value | Accessor | v1 source |
  |---|---|---|---|
  | `_OUTPUT_TOKENS_HARD_CAP` | `65536` | `max_output_tokens()` | `_mt_hard_cap` in the pre-estimate clamp. It bounds even orgs whose `max_response_tokens` is unset. |
  | `_DEFAULT_RESPONSE_TOKEN_CEILING` | `4096` | `default_response_token_ceiling()` | `org_config.get("max_response_tokens", 4096)`. |
  | `_MAX_OUTPUT_TOKENS_BOUNDARY` | `1_000_000` | `max_output_tokens_boundary()` | `MAX_OUTPUT_TOKENS_CEILING`, the boundary refusal for `max_tokens` and `max_completion_tokens`. |
  | `_MAX_COMPLETIONS_PER_REQUEST` | `1` | `max_completions()` | The boundary clamp `body["n"] = 1`, which v1 applies because its output guard is single-choice. |

  `_DEFAULT_RESPONSE_TOKEN_CEILING` is the org ceiling for every request in this spec. The compiled plan carries no org `max_response_tokens`, because the control→v2 FirewallConfig writer is Phase 3/GW14b. An import-time check (raising `CapacityUnavailable`) enforces `1 ≤ default ceiling ≤ hard cap ≤ boundary` and `max_completions() ≥ 1`.
- **The `n` bound is v1 parity, not a new magnitude.** v1 does cap `n`: the chat boundary rewrites any `n ≠ 1` to 1 and records `n=<n>->1` (I-20). v1's TPM pre-charge, `_estimate_request_tokens(prompt, max_tokens)`, takes no `n` at all. So v1 charges and forwards exactly one completion, and `max_completions() = 1` reproduces that. A larger `n` would multiply the lease cap and overdraft bound by a value that neither v1 nor the runbook declares.
- `max_request_tokens() -> int` returns `estimate_request_tokens(max_request_bytes(), max_output_tokens(), max_completions())`, which is `max(1, max_request_bytes() // D) + 65536 × 1`. It uses the hard cap, not the 4096 default, because the hard cap is the largest value the clamp can produce for *any* org ceiling. The bound therefore stays valid when Phase 3 supplies an org `max_response_tokens` above 4096. Characters are at most bytes (the C26 pre-check bounds the body), the clamped `max_tokens` is at most the hard cap, and the clamped `n` is 1. So this upper-bounds the cost of every request that passes parsing.
- `_MAX_OUTPUT_TO_INPUT_TOKENS` is not introduced. The earlier design's ratio default is replaced by the v1 values above.

**`edge/wire/openai.py`**
- `CanonicalRequest` gains these fields. Every default is the value v1 uses when the field is absent, so every existing construction still compiles:
  - `max_tokens: int = 0`
  - `n: int = 1`
  - `max_completion_tokens: int | None = None`
  - `request_clamps: tuple[str, ...] = ()`, the v1 I-20 signalled-mutation record
- New declared codes: `INVALID_MAX_TOKENS = "invalid_max_tokens"` and `INVALID_MAX_COMPLETION_TOKENS = "invalid_max_completion_tokens"`, using v1's spellings.
- `MalformedRequest` gains `param: str | None = None`, keyword-only and defaulted, so every existing raise compiles unchanged. The lifecycle's existing parse-refusal render site passes `param=exc.param` to `edge/errors.render`, which already accepts `param`.
- `OpenAIRequestParser.parse` reads `"max_tokens"`, `"max_completion_tokens"` and `"n"` through one helper, `_int_field(obj, key, *, default, minimum, maximum=None, over_code=None)`:
  - An absent key or JSON `null` gives the default. This is v1's "explicit `null` is treated identically to absent".
  - A present value must be a JSON integer (not a bool, not a float) at or above its minimum (0 for both token fields, 1 for `n`). Otherwise the parse raises `MALFORMED_WRONG_SHAPE`. v1 refuses a non-integral float and rejects negatives with `invalid_max_tokens`, but it coerces `"100"` and `100.0` and silently coerces a bad `n` to 1. The v2 parser keeps its stricter fail-closed shape discipline, as it does for `stream`.
  - Both token fields pass `maximum=ResourceContract.max_output_tokens_boundary()`. A value above it raises `MalformedRequest(over_code, param=key)`, which is `invalid_max_tokens` / `invalid_max_completion_tokens` with `param` `max_tokens` / `max_completion_tokens`. This is v1's boundary refusal. Parsing runs before identity and admission, so the refusal renders with `request_id` null (as every parse code does, 3.12), opens no admission slot, spends no budget and makes zero provider calls.
  - A value at or below the boundary is accepted here and clamped later. It is never refused for being merely large.
  - `max_tokens = 0` stays 0, v1's scan-only sentinel.
- New pure `clamp_output_budget(req, *, org_ceiling: int) -> CanonicalRequest` implements v1's clamp, with fields in v1's order:
  ```
  REQUIRE org_ceiling ≥ 1                                   // else ValueError (programming error)
  clamps := []
  n'  := min(req.n, ResourceContract.max_completions())     // v1: n forced to 1
  IF n' ≠ req.n THEN clamps.append("n=" + req.n + "->" + n')
  ceiling := min(org_ceiling, ResourceContract.max_output_tokens())
  mt' := min(req.max_tokens, ceiling)                       // changes only when > ceiling ≥ 1, i.e. only when > 0
  IF mt' ≠ req.max_tokens THEN clamps.append("max_tokens=" + req.max_tokens + "->" + mt')
  RETURN replace(req, n=n', max_tokens=mt', request_clamps=req.request_clamps + clamps)
  ```
  A second application changes nothing and appends nothing, so the clamp is idempotent. `max_completion_tokens` is not touched here (see below).
- Only `max_tokens` is charged, as in v1. `max_completion_tokens` is read and boundary-checked, then carried on the request uncharged (see "Found, not fixed"). v1 clamps it only in its provider-forwarding step: `min(value, max_response_tokens)` when it is the only output budget, with no `max_tokens` injected beside it (SEAM-A). That step belongs to Phase 2 here.
- **Phase 2 boundary.** Forwarding the clamped `max_tokens` and `n`, and v1's SEAM-A `max_completion_tokens` clamp, are provider egress (the `HttpProviderClient`, Phase 2). The Phase 1 stub provider consumes text deltas only. Phase 2 must forward the clamped values, never the parsed ones, or it would under-charge. Surfacing `request_clamps` in a response header or the audit record is Phase 3.
- New pure `prompt_chars(req) -> int`: the summed length of `req.input_channels` texts, plus one separator character for each additional content part of the same message (v1 joins list content parts with `" "`). For a request whose only text is message content this is exactly v1's `prompt_chars`. Other folded text channels are counted too, which charges at least v1 and never less.

**`runtime/store_keys.py`**
- `budget_workers(org)`: a ZSET under the `{rv2}` tag, scored by store-clock lease expiry.

**`admit/quota.py`**
- `derive_lease_chunk` is unchanged. It is the request-count in-flight bound, so its pinned property test still passes; the docstring is corrected to say so.
- New pure `derive_lease_cap_tokens(contract, *, q_safe) = derive_lease_chunk(...) × contract.max_request_tokens()` is the contract bound. The fair share (`pool // active_workers`) is the binding term in practice. This cap is runbook-derived (GW06: the chunk "derives from the ResourceContract"; C29: at most remaining / active workers). v1 cannot decide it, because v1 has no lease and charges a per-request Redis Lua TPM bucket.
- `QuotaComponent.refund(cost, *, request_id)` delegates to the lease. It is local and has no store I/O.

**`admit/lease.py`**
- `acquire`: inside the existing WATCH/MULTI, read `TIME`, `ZREMRANGEBYSCORE(budget_workers, -inf, now)` and `ZCARD`, then compute `active = max(1, card + (self not present))` and `grant = min(pool, cap, max(1, pool // active))`. In the same MULTI, `ZADD` this worker with expiry `now + ttl`.
- `return_unspent`: `ZREM` the worker. A crashed worker drops out at TTL on the store's clock (R2-09).
- These are not whole-collection commands, so `check_tenant_scale` passes.
- New keyword `overdraft_tokens: int | None = None` on `BudgetLease`; `None` means 0, which is today's behaviour for every existing construction. In `try_spend`, if `remaining ≥ cost` it spends as today. Else, if `remaining > 0` and `cost - remaining < overdraft_tokens`, it spends and lets `remaining` go negative, then schedules a refill. Otherwise it refuses as today, spending nothing. The refill's `remaining += grant` settles the debt. The overshoot is published on the existing `amf_quota_lease_overshoot`.
- `refund(cost, generation)`: `remaining += cost` iff `generation == state.generation`. It is local, with no store I/O.

**`edge/errors.py`** (Group F codes)
- Map `invalid_max_tokens` and `invalid_max_completion_tokens` to `(400, "invalid_request_error")`. They are keyed as literal strings, like the GW16 misc-surface codes, so the render table takes no upward import. The comment cites v1's boundary validator (HTTP 400 with `param` naming the field). Each code gets a declared `Error_Frame` message stating the field must not exceed the boundary, with the number rendered from `ResourceContract.max_output_tokens_boundary()`, not written as a literal.

**`edge/openai_chat.py::_estimated_cost` and `edge/openai_misc.py::_estimated_cost`**
- After the plan is pinned and before the budget phase, the chat lifecycle replaces `req` with `clamp_output_budget(req, org_ceiling=ResourceContract.default_response_token_ceiling())`. That is one call line, and Phase 3 swaps the argument for the plan's org value. Streaming and non-streaming share it. Every later phase sees only the clamped request.
- Chat returns `ResourceContract.estimate_request_tokens(prompt_chars(req), req.max_tokens, req.n)` over the clamped `req`.
- Embeddings returns `ResourceContract.estimate_request_tokens(sum(len(item) for item in req.input_items), 0, 1)`: the estimate over the input items with `max_tokens = 0` and `n = 1`.
- The edge wiring constructs the lease with `overdraft_tokens=contract.max_request_tokens()`.

**`edge/dev_admit.py` and `edge/dev_profile.py`**
- `DevLeaseBudget.evaluate` charges the real cost (2.18), the same clamped cost the default lifecycle charges. Delete `_DEV_COST_PER_REQUEST`.
- `AMF_DEV_BUDGET_POOL_REQUESTS` is replaced by `AMF_DEV_BUDGET_POOL_TOKENS`. The old name refuses to start with a message naming the replacement.
- `DevLeaseBudget.refund` delegates. `DevUnlimitedBudget.refund` is a no-op.
- The boot log loses the "1 unit PER REQUEST" and "input REDACT NOT masked" notes.

### Order of changes (bottom-up by layer)

1. **domain**: `FindingDisposition.finding_index`.
2. **runtime**:
   - `_CHARS_PER_TOKEN_DIVISOR` and `estimate_request_tokens`
   - the four v1 output-budget constants, their accessors (`max_output_tokens`, `default_response_token_ceiling`, `max_output_tokens_boundary`, `max_completions`) and the import-time ordering check
   - `max_request_tokens`
   - `store_keys.budget_workers`
3. **dispatch**: `verify._resplit` and the index-keyed obligations; `authorized.verified_upstream`, with `dispatch()` refactored onto it.
4. **resolve**: `finding_index` and REDACT-span coalescing (touching spans never merged).
5. **detect**: `confusables.py`; canonicalize fold; patterns (validator, bounds, lead alternatives, version record); matcher (validator plus region rescan); GW10 re-export.
6. **admit**: `queues.remove`; `admission.complete`; `lease` (fair-share grant, worker ZSET, overdraft, refund); `quota.refund` and `derive_lease_cap_tokens`.
7. **edge**:
   - `wire/openai`: message index; `MalformedRequest.param`; `max_tokens` / `max_completion_tokens` / `n` parsing with the `1_000_000` boundary codes; `request_clamps`; `clamp_output_budget`; `prompt_chars`
   - `wire/openai_misc`: `MiscEmbeddingsRequest.input_is_string`
   - `chat_deps` protocols
   - `request_holds`, `phase_resolvers` and `input_enforcement`
   - `embeddings_input`
   - `openai_chat`, `routes`, `openai_nonstream`, `error_frame_scan` and `openai_misc` wiring
   - `errors` mapping (`input_unmaskable`, `invalid_max_tokens` and `invalid_max_completion_tokens` → 400 `invalid_request_error`)
   - the clamp call in the chat lifecycle, before the budget phase
   - dev profile cleanup
8. **Test reconciliation**, then the full gate.
9. **Records**: plan doc `docs/plans/<date>-v2-enforcement-leaks.md`, evidence directory, and an AGENTS.md changelog entry, in the house format.

Each layer step must leave import-linter, mypy, ruff and the AST gates green before the next starts. `check_sizes` must pass after each edge step, with `openai_chat.py` and `openai_misc.py` no longer than they are today.

## Testing Strategy

### Validation Approach

The strategy has two phases. First, exploratory tests surface counterexamples on the unfixed tree. Most are already reproduced (B and C live; A by the existing xfail). They are kept as permanent regression tests. Then fix-checking and preservation property tests run on the fixed tree. Property tests follow the house idiom: a seeded `random.Random`, at least 10,000 iterations, no hypothesis, and the seed in every assertion message.

### Exploratory Bug Condition Checking

**Goal.** Fail on unfixed code, then confirm or refute each root cause.

**Test plan.** Each file below is written and run against the unfixed tree before its fix lands.

**Test cases (expected to fail on unfixed code):**
1. **A, channels.** `tests/edge/test_v2_leaks_a_input_redact.py` uses a recording provider and the dev plan. It plants an SSN in a user message, a system message, a second message, and split across two `content_part_text` parts, streaming and non-streaming. It asserts the raw value is absent from every recorded delta and `[REDACTED]` is present.
2. **A, embeddings.** `tests/edge/test_v2_leaks_a_embeddings_redact.py` uses a recording provider and the dev plan. Cases:
   - a list input `["release notes", "My SSN is 123-45-6789"]` (today the raw SSN reaches the provider)
   - a string input `"mail jane@example.com"` (today raw)
   - a list where two items each carry a value (today both raw)

   It asserts one delta per item in order, `[REDACTED]` in each redacted item, the clean item byte-identical, and no raw value in any delta.
3. **A, verify.** `tests/dispatch/test_v2_leaks_verify.py`:
   - a `_resplit` length-change case, where today delta 1's first byte moves into delta 0
   - a mixed `SECRET → flag` plus `CREDENTIAL → redact` plan, which today gives a false `VerificationFailure`
   - a no-op transformation, which must give `input_unmaskable` (HTTP 400 `invalid_request_error`) and zero calls
4. **B.** `tests/detect/test_v2_leaks_matcher.py` uses the three PAN spellings (today `[]`), a Luhn-invalid PAN (none expected), and 12- and 20-digit runs (none expected).
5. **C.** The same file covers `you are DAN`, `you are DАN`, `enable developer mode`, `answer without any restrictions` (all `[]` today) and `Ιgnore all previous instructions` (no hit today).
6. **D.** `tests/admit/test_v2_leaks_admission.py` covers `complete("unknown")` (today it pops a foreign slot) and in-flight growth after N completes. `tests/edge/test_v2_leaks_d_lifecycle.py` uses a counting admission across every exit path, chat and embeddings (today 0 completes).
7. **E.** `tests/edge/test_v2_leaks_phase.py` uses a plan with an input-only REDACT PII rule and an output-only BLOCK SECRET rule. The dev boot accepts it (today it refuses). Programmatically, the input-only rule must not touch output.
8. **F.** `tests/admit/test_v2_leaks_budget.py`:
   - a 2,000-character prompt with a request-count chunk (today refused)
   - a request with `max_tokens=4096, n=8` (today charged only its characters)
   - a request with `max_tokens=2000000` (today accepted and forwarded; expected `invalid_max_tokens`, 400)
   - an input BLOCK after a spend, on chat and embeddings (today no refund)
   - a grant with 4 active workers (today it equals the chunk, not `pool // 4`)

**Expected counterexamples.** Raw PII in the chat and embeddings provider bodies. Empty scans for PANs and jailbreak phrases. Queue depth that only grows. Phase leakage. A false `budget_unavailable`, and an output budget never charged. If any of these does not reproduce, the matching root cause is re-hypothesised before fixing.

### Fix Checking

```
FOR ALL X WHERE isBugCondition(X) DO
  result := gateway_F'(X)
  ASSERT expectedBehavior(result)   // Properties 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16
END FOR
```

### Preservation Checking

```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT gateway_F(X) = gateway_F'(X)   // Properties 2, 13, 14
END FOR
```

**Approach.** For detection, `tests/detect/_gw09_original_oracle.py` holds a frozen copy of the original `RULESET` prefilter and precise strings, `_HOMOGLYPHS`, and `scan`. The oracle is the "unfixed function". For the token cost, `tests/admit/_v1_token_estimate_oracle.py` holds a frozen copy of v1's `estimate_prompt_tokens`, because `gateway_v2` tests do not import the v1 package. Lifecycle preservation reuses the shipped `test_lgw15_*`, `test_lgw19_*`, `test_lgw06_*`, GW13 and GW16 suites unchanged (including `test_gw16_embeddings_block.py` for 3.16), apart from the reconciliations listed below.

**Preservation test cases:**
1. **Detection superset (Property 13).** 10,000 seeded texts drawn from the GW09 token pool, fixtures and random alphabets. Assert oracle ⊆ fixed. Assert equality when no failed-confirm, lead phrase or new confusable is present.
2. **Backtick and dev FPR.** LGW09-1 and LGW09-6 stay at zero terminal blocks and dev-traffic FPR 0.0 with the larger confusable set.
3. **Enforcement (Property 14).** 10,000 seeded chat requests and 10,000 seeded embeddings requests with ALLOW, FLAG or BLOCK input under `scope: both` plans. Chat provider deltas must be byte-identical to `req.text_deltas`. Embeddings provider deltas must equal `embeddings_deltas(req)` item for item. Codes and statuses must match. The default boot table test is unchanged.
4. **Resolver coalescing.** For non-overlapping transformations, `resolve` returns the same `Decision` as before, except that `finding_index` is now set.

### Unit Tests

- Luhn validator vectors, the PAN separator grammar, and the 13- and 19-digit length bounds.
- The start-superset invariant: for each precise alternative, a generated match string is matched by the prefilter at the same start.
- `_resplit` with growth, shrink, straddling spans and empty deltas.
- Resolver coalescing never merges touching spans across a delta boundary.
- `verified_upstream`: a forged authorization raises, and the BLOCK path is unreachable because `authorize` returns `None`.
- `stage_embeddings`: a delta-count mismatch and a `VerificationFailure` both return `input_unmaskable`; `input_is_string` with more than one delta returns `input_unmaskable`.
- `parse_embeddings` sets `input_is_string` for a string input and not for a one-element list.
- `BoundedQueue.remove` keeps the depth and age deques aligned.
- `complete` with unknown, repeated and colliding ids.
- `RequestHolds.release` is idempotent and never raises.
- `estimate_request_tokens`: `(0, 0, 1) == 1`; `(3, 0, 1) == 1`; `(8, 0, 1) == 2`; `(2000, 256, 2) == 1012`; `(0, 100, 3) == 300`; negative inputs and `n = 0` raise. These cases test the pure function. Surfaces call it with the clamped `n = 1`. `_CHARS_PER_TOKEN_DIVISOR == 4`.
- `prompt_chars` joins a message's content parts with one separator, matching v1.
- The parser's `max_tokens` / `max_completion_tokens` / `n` grammar:
  - absent and `null` give 0, `None` and 1
  - a bool, a float, a string, a negative token field or `n = 0` raise `MALFORMED_WRONG_SHAPE`
  - `max_tokens = 1_000_000` is accepted; `1_000_001` raises `invalid_max_tokens` with `param == "max_tokens"`
  - `max_completion_tokens = 1_000_001` raises `invalid_max_completion_tokens` with `param == "max_completion_tokens"`
- `clamp_output_budget` with `org_ceiling = 4096`:
  - `max_tokens` 0 → 0, 100 → 100, 4096 → 4096 and 1_000_000 → 4096 (recorded `"max_tokens=1000000->4096"`)
  - `org_ceiling = 100_000` with `max_tokens = 1_000_000` → 65536 (the hard cap binds)
  - `n` 1 → 1 with no record; 8 → 1 recorded `"n=8->1"`
  - applying it twice equals applying it once; `org_ceiling = 0` raises
- The output-budget constants: `max_output_tokens() == 65536`, `default_response_token_ceiling() == 4096`, `max_output_tokens_boundary() == 1_000_000`, `max_completions() == 1`, and the ordering check holds.
- `max_request_tokens() == max(1, max_request_bytes() // 4) + 65536`. For example, at `per_worker_rss = 2 GiB` it is `1_342_177 + 65_536 = 1_407_713`.
- The `invalid_max_tokens` and `invalid_max_completion_tokens` envelopes render HTTP 400 with `error.type` `invalid_request_error`, the body code, `param` set, and `request_id` null.
- The dev env rename refuses with a message.
- The `input_unmaskable` envelope renders HTTP 400 with `error.type` `invalid_request_error` and body code `input_unmaskable`, the same family as `input_blocked`.

### Property-Based Tests (seeded `random.Random`, ≥ 10,000 iterations)

- **P3** random multi-message, multi-part requests with planted values, checked at the provider.
- **P4** random no-op, out-of-range and overlapping transformations, on chat and embeddings, plus forged delta-count mismatches on embeddings.
- **P5** random PANs (valid and invalid Luhn) with random separators and surrounding text.
- **P6** order independence against a single-pattern reference scan.
- **P7 and P8** random confusable substitution plus the span-redaction removal check. A table mutation must change the version.
- **P9** random exit-path schedules against the real `AdmissionController`, over chat and embeddings.
- **P10** random admit and complete interleavings, including collisions.
- **P11** random rule scopes, findings and phases against `resolve`.
- **P12** N leases over one fakeredis with random costs, refunds, crashes and returns (ledger conservation), plus random `(messages, max_tokens, n)` checked against the frozen v1 estimate oracle.
- **P16** random `max_tokens`, `max_completion_tokens` and `n` from the full value grammar (absent, `null`, 0, in range, at and around 4096, 65536 and 1_000_000, above the boundary, off-contract) and random org ceilings of at least 1. Checks: refusal code and `param`; the clamped values never exceed their bounds; clamp idempotence; one record per changed field; and a charged cost of at most `max_request_tokens()`. Each refused case also runs through the in-process lifecycle with a recording provider, counting admission and lease, and must show zero provider calls, zero admits and an unchanged lease.
- **P15** random string and list embeddings inputs (0 to 32 items) with random planted values in random items, checked at the provider: one delta per item, order kept, planted bytes absent, clean items byte-identical.
- **P13 and P14** preservation, as above.
- A structural cost check: the number of matcher regex invocations is at most `c·len(text)` on adversarial corpora (digit runs, `x` runs, `a@` runs) at the decode-budget size. Wall-clock numbers go to the evidence, not into assertions.

### Integration Tests

- Dev-profile in-process ASGI for `curl`-equivalent flows, streaming and non-streaming:
  - SSN is masked at the provider and the client (the xfail marker is removed and the test must pass)
  - an AWS key gives `input_blocked` and zero calls
  - a PAN gives `input_blocked` (credential → block)
  - `you are DАN` produces a JAILBREAK FLAG and is forwarded
- Dev-profile `/v1/embeddings`:
  - a list input with an SSN in one item reaches the provider with that item masked and the other items byte-identical
  - a string input with an email reaches the provider as one masked delta
  - a REDACT whose verification is forced to fail returns HTTP 400 `invalid_request_error` with code `input_unmaskable`, zero provider calls, and a refund on the lease
  - an AWS key in any item still gives `input_blocked` with zero calls
- Dev-profile chat output budget, streaming and non-streaming:
  - `max_tokens=2000000` gives HTTP 400 `invalid_max_tokens`, `param` `max_tokens`, `request_id` null, zero provider calls, an unchanged queue depth and an unchanged lease
  - `max_completion_tokens=2000000` gives HTTP 400 `invalid_max_completion_tokens` in the same way
  - `max_tokens: null` behaves exactly like an absent `max_tokens`
  - `max_tokens=100000, n=4` is admitted, charged `input + 4096`, and its request carries `request_clamps == ("n=4->1", "max_tokens=100000->4096")`
- 3 × `request_depth` sequential requests through the real `AdmissionController`: no shed, and queue depth returns to 0 without `DevServedApp` slot logic.
- Cancellation mid-stream and a kill-switch cut each complete exactly once.
- Embeddings: admitted, then completed and refunded on input BLOCK, scan failure and `input_unmaskable`.
- Full gate: `pytest` offline, `mypy --strict`, ruff, `lint-imports`, `check_sizes`, and the seven AST gate CLIs. The expected result is the 1948-test baseline plus the flipped xfail plus new tests passing, 95 skipped, and **1 xfailed** (the R2-04 `store_ahead` strict xfail is unchanged).

### Shipped tests that encode the defect (reconciled, not weakened)

| Test | Today it asserts | After the fix |
|---|---|---|
| `tests/edge/test_dev_profile_pipeline.py::test_input_redact_masks_pii_before_the_provider` | strict xfail | marker removed, so it passes |
| `tests/detect/test_deterministic_detectors_gw09.py::test_property6_*` | reference = leftmost-first; token `4111111111111111` emits nothing | reference adds the region-rescan and Luhn semantics |
| `…::test_property7_*` | `_hash_model` over patterns only | model appends the confusables record and the validator field |
| `tests/detect/fixtures_gw09.py` | card comment "NOT emitted by scan" | updated; credential recall now 1.0 |
| `tests/admit/test_lgw19_admission.py` (Property 1 loop) | `_in_flight[rid].finished is True` | `rid not in _in_flight` (pruned) |
| `tests/edge/test_dev_profile_components.py::test_bad_plan_file_refuses_to_start[scope='input']` | refusal | moved to a positive boot test |
| `tests/edge/test_dev_profile_boot.py::test_served_app_releases_admission_slots_per_request` | wrapper releases slots | replaced by the lifecycle-level slot test |
| `…::test_lease_budget_admits_until_the_pool_is_spent_then_refuses` | 1 unit per request, `pool_requests` | token pool and cost |
| `…::test_dev_boot_wires_real_components_and_logs_each_loudly` | old log strings | new log strings |
| Admission and budget test doubles (21 fakes across 11 edge test files, including the `test_gw16_embeddings_*` fakes) | `admit` and `evaluate` only | gain no-op `complete` and `refund` (protocol extension) |

`tests/edge/test_lgw12_errors.py` reads each status from the `_ENVELOPE` table, so it picks up the new `input_unmaskable`, `invalid_max_tokens` and `invalid_max_completion_tokens` entries unchanged. `tests/edge/test_lgw15_lifecycle.py` asserts the charged cost is a non-negative int, which still holds (the estimate is at least 1). No shipped embeddings test asserts that a REDACT input is forwarded raw; the BLOCK properties in `test_gw16_embeddings_block.py` are unchanged.

## Open questions

There are no open questions. The last one, the per-request output-token ceiling, is resolved by gateway v1 parity (first recorded decision below). Every other point is decided by the runbook or by v1, and each decision names its source.

### Recorded decisions (formerly open)

- **Per-request output-token ceiling: v1's clamp, boundary and `n = 1`. Resolved by v1 parity.**
  - **The decision.** `runtime/resources.py` declares v1's hard cap `65536`, its default org response-token ceiling `4096`, its boundary `1_000_000` and its `n = 1`. All four come from `gateway/ai_mesh_gateway/main.py`: the pre-estimate clamp, `MAX_OUTPUT_TOKENS_CEILING`, and the boundary `n` clamp.
  - **Edge behaviour.** The edge refuses `max_tokens` or `max_completion_tokens` above `1_000_000` with HTTP 400 `invalid_max_tokens` / `invalid_max_completion_tokens` and `param` naming the field. It treats JSON `null` as absent. It clamps `max_tokens` to `min(value, org ceiling, 65536)` and `n` to 1 before the cost estimate, and records each clamp in `request_clamps`. The org ceiling is 4096 until the Phase 3/GW14b FirewallConfig writer puts an org `max_response_tokens` on the plan. The design keeps its stricter `MALFORMED_WRONG_SHAPE` for non-integer or negative values, where v1 coerces or uses `invalid_max_tokens`.
  - **Resulting bounds.** `max_request_tokens() = max(1, max_request_bytes() // 4) + 65536 × 1`, which is finite and v1-derived. Both the overdraft bound (`overdraft_tokens = max_request_tokens()`) and the lease cap (`derive_lease_chunk × max_request_tokens()`) are now finite and v1-derived. Under the earlier ratio default the output term equalled the input-token bound, about 1.34M tokens at a 2 GiB worker RSS. It is now 65,536.
  - **Consequence, recomputed.** Every request that passes parsing costs at most `max_request_tokens()`: its characters are bounded by the C26 byte pre-check, its `max_tokens` by the hard cap, and its `n` is 1. `try_spend` admits whenever `remaining > 0` and `cost − remaining < overdraft_tokens`. With `remaining ≥ 1`, `cost − remaining ≤ max_request_tokens() − 1`, so that check always passes. The earlier consequence, "a request too large for the lease within the overdraft bound is refused even if the org pool has room", **no longer applies**. One case still applies, and it is unchanged by this decision: a lease at or below zero (spent, or in overdraft debt) refuses `budget_unavailable` until its async refill lands, even when the org pool has room. That is the shipped GW06 low-watermark refill window (3.11), not a consequence of the ceiling.
  - **Later phases.** Forwarding the clamped `max_tokens` and `n`, and v1's SEAM-A `max_completion_tokens` forwarding clamp, are Phase 2. Surfacing `request_clamps` is Phase 3.
- **`input_unmaskable` status: HTTP 400 `invalid_request_error`.** Decided by v1 parity: v1's `_build_safe_block_response` re-maps content-category 403s to `GATEWAY_BLOCK_STATUS`, which defaults to 400, so the stock OpenAI SDK raises `BadRequestError`. It also follows the frozen GW01 wire contract (§10.2.2) and the reconciliation `edge/errors.py` already applies to `input_blocked`.
- **Lease cap value: `derive_lease_chunk × max_request_tokens`, with the fair share binding.** The formula is runbook-derived (GW06, C29). v1 cannot decide the formula, because v1 has no lease and charges a per-request Redis Lua TPM bucket. Its `max_request_tokens` factor is now finite and v1-derived (previous decision). An owner-signed tighter cap would change only `derive_lease_cap_tokens`.
- **Token cost: v1's `estimate_prompt_tokens`.** The request cost is `max(1, input + max_tokens × n)` with `input = max(1, chars // 4)` when there are characters, else 0. `D = 4` is declared in `runtime/resources.py`. Absent fields default to `max_tokens = 0` and `n = 1`. The estimate runs over the clamped `max_tokens` and `n` (first decision above), as v1's does. Embeddings charge the estimate over the input items with `max_tokens = 0` and `n = 1`. Unlike v1, an off-contract `max_tokens` or `n` is refused `MALFORMED_WRONG_SHAPE` rather than silently coerced, because coercion under-charges.
- **Embeddings input REDACT: in scope.** Gateway v1 masks every embedding input item before the provider and fails closed when it cannot (`_scan_redact_embedding_inputs`). This spec does the same through the one chat redaction path, item by item, with the shape preserved (Group A, Property 15).
- **Full `AuthorizedProviderEntry` on the chat path: deferred to Phase 2/3.** This spec routes chat and embeddings requests through the entry's request-side half (`verified_upstream`): provenance assertion, apply and verify, transform. Pool lease, audit-started and the REWRITE seam stay with Phase 2 (provider egress) and Phase 3 (GW14b audit and console). v1's chat path does audit and pool providers, and v1 parity is the target, but v1 does not decide the phasing.

## Found, not fixed (recorded for later cards)

- **Content-hash request ids collide.** `ChatSurface._request_id` and `EmbeddingsSurface._request_id` give two concurrent identical requests the same id. This spec makes `complete` refcount-safe, but a per-request nonce (the PIPELINE-0023 lesson) belongs to a later card.
- **Detection runs on the event loop.** `_input_scan` (and now `scan_deltas`) runs canonicalisation and matching inline (C38). The prefilter bounds remove the measured 63 s worst case, but offloading the scan is GW13/C38 work.
- **Each family detector canonicalises separately.** The dev adapter `DeterministicFindings` runs PII, secret and injection detectors, and each canonicalises on its own, so a channel value is canonicalised three times. This is within each detector's one-pass contract. A shared pass is an optimisation, not this fix.
- **`max_completion_tokens` is not charged.** The newer OpenAI field for the output budget is not read by v1's estimate, and this spec keeps v1 parity. A client that sends `max_completion_tokens` instead of `max_tokens` is charged only its input. Charging the larger of the two belongs to the Phase 2 true-up card. **Fixed here, as in v1:** the field is parsed, JSON `null` is treated as absent, and a value above `1_000_000` is refused HTTP 400 `invalid_max_completion_tokens` with `param` `max_completion_tokens` (v1's SEAM-A boundary validator). The uncharged gap is therefore bounded by the 1,000,000-token boundary at parse time, and by the org ceiling once Phase 2 applies v1's forwarding clamp.
