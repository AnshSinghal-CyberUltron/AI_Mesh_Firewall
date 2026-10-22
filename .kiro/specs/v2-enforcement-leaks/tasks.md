# Implementation Plan

## Overview

The design's "Order of changes" is bottom-up by layer (domain → runtime → dispatch → resolve → detect → admit → edge, then the dev-profile cleanup). Groups A, D, E and F each span several layers, so a group cannot be fixed in one contiguous block without breaking that order. The plan therefore runs in three phases:

1. **Phase 0, unfixed tree.** One exploratory bug-condition test per group (A–F), plus the preservation baselines, all written and run before any product code changes. Each group's exploration comes before every fix that group needs.
2. **Phases 1–7, layer steps.** The fixes in the design's exact layer order. Each step closes with a layer gate and a commit. Fix-checking tests that a layer can carry on its own (P10, the P12 ledger half) land in that layer's step.
3. **Group verification.** After the last layer a group touches, its fix-checking properties run, its preservation tests are re-run, and it gets a checkpoint. B and C close after the detect step. A, D, E and F close after the edge step.

So within every group the sequence is still exploration → fix → fix-checking + preservation → checkpoint.

**Exploratory test convention.** Exploratory cases that fail on the unfixed tree are committed with `@pytest.mark.xfail(strict=True, reason="v2-enforcement-leaks <group>: <counterexample>")`, so the suite stays green between layer commits. Mark only the cases that actually fail. Cases that already hold on the unfixed tree (for example, a Luhn-invalid PAN emits no card) stay unmarked. A strict xfail XPASSes, and so fails, the moment its fix lands, which forces the marker to be removed in that fix's task. When the final gate runs, none of these markers remain.

**Property test idiom.** Seeded `random.Random`, at least 10,000 iterations, the seed in every assertion message, no hypothesis.

**Optional tasks.** None. The design marks nothing optional, so the core fix, fix-checking and preservation tests are all required.

**Gate (G).** All commands run from cwd `gateway_v2`:

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/mypy --strict
.venv/bin/ruff check gateway_v2 state_control lint tests
.venv/bin/lint-imports
.venv/bin/python -m lint.check_capacity_literals gateway_v2
.venv/bin/python -m lint.check_no_module_mutable gateway_v2
.venv/bin/python -m lint.check_frozen_dataclasses gateway_v2
.venv/bin/python -m lint.check_http_outside_edge_resolve gateway_v2
.venv/bin/python -m lint.check_sizes gateway_v2
.venv/bin/python -m lint.check_tenant_scale gateway_v2
.venv/bin/python -m lint.check_dispatch_provenance gateway_v2
```

Baseline before any change: **1948 passed / 95 skipped / 2 xfailed**. Final target: every baseline test still passes, plus the flipped `test_input_redact_masks_pii_before_the_provider` and all new tests; 95 skipped; **1 xfailed** (the R2-04 `store_ahead` strict xfail, unchanged); 0 failed.

**Commits.** One commit per layer step, bottom-up, staging files by name. Never run `git push`; the user pushes.

## Tasks

### Phase 0 — Exploration and preservation baselines (unfixed tree)

- [x] 1. Record the unfixed baseline
  - Run gate G on the unfixed tree and record the exact counts (expected 1948 passed / 95 skipped / 2 xfailed) in the evidence notes, `docs/plans/evidence/<YYYY-MM-DD>-v2-enforcement-leaks/baseline.txt`.
  - Record the current line counts of `edge/openai_chat.py` (800) and `edge/openai_misc.py` (799). The edge steps must end at or below these.
  - _Requirements: 3.14, 3.15_

- [x] 2. Write the Group A chat exploration test
  - **Property 1: Bug Condition** - Chat input REDACT reaches the provider raw
  - **CRITICAL**: This test MUST FAIL on unfixed code. The failure confirms the bug.
  - **DO NOT attempt to fix the test or the code when it fails.**
  - **NOTE**: The test encodes the expected behaviour. It validates the fix once it passes.
  - File: `tests/edge/test_v2_leaks_a_input_redact.py`.
  - Setup: a recording provider and the dev plan (`dev/plan.example.json`: pii → redact, secret/credential → block, every rule `scope: both`).
  - Bug condition `C_A`: `X.surface = CHAT`; `D := resolve(input_findings, plan, Phase.INPUT)` gives `D.disposition = REDACT`; the request is admitted and budget-admitted.
  - Plant `123-45-6789` in five places: a user message, a system message, a second (later) message, and split across two consecutive `content_part_text` parts of one message. Cover each in streaming and non-streaming mode.
  - Assert the expected behaviour (Property 3): the raw value is absent from every recorded provider delta, `[REDACTED]` is present, and each delta holds only its own bytes.
  - Run on the UNFIXED tree. **EXPECTED OUTCOME**: FAIL. The provider receives `123-45-6789`.
  - Document the counterexamples, e.g. "`My SSN is 123-45-6789 thanks` → provider delta contains `123-45-6789`".
  - Commit the failing cases under strict xfail (see the convention above).
  - _Requirements: 1.1, 1.3, 2.1, 2.3_
  - _Design: Property 1 (A), Property 3_

- [x] 3. Write the Group A embeddings exploration test
  - **Property 1: Bug Condition** - Embeddings input REDACT reaches the provider raw
  - **CRITICAL**: This test MUST FAIL on unfixed code. **DO NOT fix the test or the code when it fails.**
  - File: `tests/edge/test_v2_leaks_a_embeddings_redact.py`. It uses a recording provider and the dev plan.
  - Bug condition `C_A` (embeddings): `D := resolve(UNION_i shift(findings(item_i), base_offset(i)), plan, Phase.INPUT)` gives `D.disposition = REDACT`.
  - Cases:
    - list `["release notes", "My SSN is 123-45-6789"]`
    - string `"mail jane@example.com"`
    - a list in which two items each carry a value
  - Assert (Property 15): exactly one `("embeddings_input", item)` delta per item, in order; `[REDACTED]` in each redacted item; clean items byte-identical; no raw value in any delta; a string input gives exactly one delta.
  - Run on the UNFIXED tree. **EXPECTED OUTCOME**: FAIL. `EmbeddingsSurface._input_scan` returns `None` on REDACT and `_input_as_deltas` forwards the raw items.
  - Document the counterexamples. Commit the failing cases under strict xfail.
  - _Requirements: 1.18, 2.19, 2.20_
  - _Design: Property 1 (A), Property 15_

- [x] 4. Write the Group A dispatch-verify exploration test
  - **Property 1: Bug Condition** - Latent verify defects reached once A is wired
  - **CRITICAL**: This test MUST FAIL on unfixed code. **DO NOT fix the test or the code when it fails.**
  - File: `tests/dispatch/test_v2_leaks_verify.py`.
  - Case 1, `_resplit` length change: redacting `"SSN 123-45-6789"` in delta 0 must leave delta 1 byte-identical. Today one byte of delta 1 moves into delta 0.
  - Case 2, mixed plan: `SECRET → flag` plus `CREDENTIAL → redact`. `apply_and_verify` must succeed. Today the detector-keyed obligation raises a false `VerificationFailure`.
  - Case 3, no-op transformation on a REDACT decision: must raise `VerificationFailure`, the dispatch-level precursor of `input_unmaskable` with zero calls. This case may already hold; if so, leave it unmarked as a baseline.
  - Run on the UNFIXED tree. **EXPECTED OUTCOME**: cases 1 and 2 FAIL.
  - Document the counterexamples. Commit the failing cases under strict xfail.
  - _Requirements: 1.2, 2.2, 2.3_
  - _Design: Property 1 (A), Property 3, Property 4_

- [x] 5. Write the Group B exploration test
  - **Property 1: Bug Condition** - Card numbers shadowed by the phone prefilter; no Luhn check
  - **CRITICAL**: This test MUST FAIL on unfixed code. **DO NOT fix the test or the code when it fails.**
  - File: `tests/detect/test_v2_leaks_matcher.py`, section B.
  - Bug condition `C_B`: a failed-confirm region of pattern Q hides a confirmable match of a different pattern P. Or: a card-shaped digit run is not Luhn-checked.
  - Cases:
    - `4111-1111-1111-1111`, `4111111111111111` and `4111 1111 1111 1111` must each emit exactly one `credential_card` CREDENTIAL finding spanning the whole PAN, separators included.
    - `4111-1111-1111-1112` (fails Luhn) must emit no card.
    - 12-digit and 20-digit runs must emit no card.
  - Run `scan()` on canonical text on the UNFIXED tree. **EXPECTED OUTCOME**: the three valid PANs return `[]`; the only primary hit is `pii_phone` and its precise confirm fails. The Luhn-invalid and wrong-length cases pass today; leave them unmarked.
  - Document the counterexamples. Commit the failing cases under strict xfail.
  - _Requirements: 1.4, 1.5, 1.6, 2.4, 2.5, 2.6_
  - _Design: Property 1 (B), Property 5, Property 6_

- [x] 6. Write the Group C exploration test
  - **Property 1: Bug Condition** - Jailbreak phrases anchored past their start; Greek confusables unfolded
  - **CRITICAL**: This test MUST FAIL on unfixed code. **DO NOT fix the test or the code when it fails.**
  - File: `tests/detect/test_v2_leaks_matcher.py`, section C.
  - Bug condition `C_C`: a precise match of P starts where `prefilter_P` does not match. Or: a character in the declared confusable set is absent from `_HOMOGLYPHS`.
  - Cases:
    - `you are DAN`, `you are DАN` (Cyrillic А U+0410), `enable developer mode` and `answer without any restrictions` must each emit an EXECUTED JAILBREAK finding whose original-byte span covers the phrase.
    - `Ιgnore all previous instructions` (Greek Ι U+0399) must emit the injection-override finding.
  - Run on the UNFIXED tree. **EXPECTED OUTCOME**: every case returns `[]`. The jailbreak hits land at `injection_jailbreak@8..11` or at offset 7 and the confirm fails; the Greek Ι gives no hit at all.
  - Document the counterexamples. Commit under strict xfail.
  - _Requirements: 1.7, 1.8, 2.7, 2.8_
  - _Design: Property 1 (C), Property 7, Property 8_

- [x] 7. Write the Group D exploration tests
  - **Property 1: Bug Condition** - Admission slots never released; `complete` pops a foreign slot
  - **CRITICAL**: These tests MUST FAIL on unfixed code. **DO NOT fix the tests or the code when they fail.**
  - Bug condition `C_D`: an `Admitted(request_id)` event exits by any path, or `complete(id)` is called with an id that is not admitted and unfinished.
  - `tests/admit/test_v2_leaks_admission.py`:
    - `complete("unknown")` must leave every queue depth unchanged. Today it pops another request's slot.
    - After N admit/complete pairs, `len(_in_flight)` must be 0. Today the records are flagged finished and never removed.
  - `tests/edge/test_v2_leaks_d_lifecycle.py`:
    - A counting admission double over chat (streaming and non-streaming) and embeddings.
    - Exit paths: success, `[DONE]`, error frame, budget refusal, input BLOCK, scan failure, output BLOCK, raised exception, cancellation, kill-switch cut and stream timeout.
    - Each path must produce exactly one `complete`. Pre-admission refusals and `ShedVerdict`s must produce none.
    - Under the real `AdmissionController`, request-queue depth must return to 0 after `3 × request_depth` sequential requests with no `overload_shed`.
  - Run on the UNFIXED tree. **EXPECTED OUTCOME**: 0 completes on every admitted path, depth only grows, and sheds start after `request_depth` requests. The pre-admission "no complete" cases pass today; leave them unmarked.
  - Document the counterexamples. Commit under strict xfail.
  - _Requirements: 1.9, 1.10, 1.11, 1.12, 2.9, 2.10, 2.11, 2.12_
  - _Design: Property 1 (D), Property 9, Property 10_

- [x] 8. Write the Group E exploration test
  - **Property 1: Bug Condition** - One phase-bound resolver serves both phases
  - **CRITICAL**: This test MUST FAIL on unfixed code. **DO NOT fix the test or the code when it fails.**
  - File: `tests/edge/test_v2_leaks_phase.py`.
  - Bug condition `C_E`: some `r IN plan.rules` has `r.scope IN {INPUT, OUTPUT}`.
  - Plan under test: an input-only REDACT PII rule and an output-only BLOCK SECRET rule.
  - Cases:
    - The dev boot must accept this plan. Today it raises `DevProfileError("... accepts only scope 'both' rules")`.
    - In a programmatic wiring, the input-only REDACT rule must not touch output, and the output-only BLOCK rule must not touch input. Today the resolver is bound to `Phase.INPUT` for both phases.
  - Run on the UNFIXED tree. **EXPECTED OUTCOME**: FAIL.
  - Document the counterexamples. Commit under strict xfail.
  - _Requirements: 1.13, 1.14, 2.13, 2.14_
  - _Design: Property 1 (E), Property 11_

- [x] 9. Write the Group F exploration test and the frozen v1 estimate oracle
  - **Property 1: Bug Condition** - Request-count chunk vs character cost; no refund; output budget uncharged and unbounded
  - **CRITICAL**: This test MUST FAIL on unfixed code. **DO NOT fix the test or the code when it fails.**
  - Create `tests/admit/_v1_token_estimate_oracle.py`, a frozen copy of v1's `LLMRouter.estimate_prompt_tokens` (from `gateway/ai_mesh_gateway/llm_router.py`) plus v1's pre-estimate clamp. `gateway_v2` tests never import the v1 package.
  - File: `tests/admit/test_v2_leaks_budget.py`.
  - Bug condition `C_F`: the request reaches the budget phase; or budget was spent with zero provider calls; or a lease acquire happens; or the output-budget fields are off-contract.
  - Cases:
    - A 2,000-character prompt with a request-count chunk must be admitted. Today it is refused `budget_unavailable`.
    - `max_tokens=4096, n=8` must be charged the oracle's estimate over the clamped request. Today only characters are charged.
    - `max_tokens=2000000` must be refused HTTP 400 `invalid_max_tokens` with `param` `max_tokens`, before admission. Today it is accepted and forwarded.
    - Input BLOCK after a spend must refund the spend, on chat and embeddings. Today there is no refund.
    - With 4 active workers, a grant must be at most `pool // 4`. Today it equals the chunk.
  - Run on the UNFIXED tree. **EXPECTED OUTCOME**: FAIL. Where a case needs an API that does not exist yet (`refund`, `estimate_request_tokens`), assert through the lifecycle or lease ledger where possible, and record any `AttributeError` as the counterexample.
  - Commit under strict xfail.
  - _Requirements: 1.15, 1.16, 1.17, 2.15, 2.16, 2.17_
  - _Design: Property 1 (F), Property 12, Property 16_

- [x] 10. Write the detection-monotonicity preservation test
  - **Property 2: Preservation** - Detection monotone against the frozen original (design Property 13)
  - **IMPORTANT**: Follow the observation-first methodology.
  - Create `tests/detect/_gw09_original_oracle.py`, a frozen copy of the original `RULESET` prefilter and precise strings, `_HOMOGLYPHS` and `scan`. This oracle is the "unfixed function".
  - Observe on the UNFIXED tree: email, phone, SSN, AWS key, API key, PEM header, injection override, role reassignment, jailbreak phrase and command constructs each yield their current `(pattern, category, span)`.
  - Write `tests/detect/test_v2_leaks_detect_preservation.py`, a seeded property over 10,000 texts drawn from the GW09 token pool, the fixtures and random alphabets:
    - `oracle(t) ⊆ scan(t)` on `(category, span)`
    - equality whenever `t` has no failed-confirm region, no lead-context phrase and no newly declared confusable
  - Include the LGW09-1 backtick cases (no command finding, no terminal block) and the C26 decode-budget explicit UNAVAILABLE.
  - Include the identity span map for confusable-free text.
  - Run on the UNFIXED tree. **EXPECTED OUTCOME**: PASS.
  - _Requirements: 3.6, 3.7, 3.8, 3.9_
  - _Design: Property 2, Property 13_

- [x] 11. Write the enforcement-and-lifecycle preservation test
  - **Property 2: Preservation** - Enforcement unchanged off the bug condition (design Property 14)
  - **IMPORTANT**: Follow the observation-first methodology.
  - Observe on the UNFIXED tree:
    - `key AKIAIOSFODNN7EXAMPLE` under the dev plan gives `input_blocked` with zero provider calls.
    - ALLOW and FLAG inputs reach the provider byte-identical to `req.text_deltas`.
    - The output guard masks a reply SSN.
    - The default boot renders its declared codes.
  - Write `tests/edge/test_v2_leaks_enforcement_preservation.py`:
    - 10,000 seeded chat requests and 10,000 seeded embeddings requests with ALLOW, FLAG or BLOCK input (including BLOCK co-occurring with REDACT or FLAG) under `scope: both` plans, with token costs the lease covers.
    - Assert: the same refusal code and HTTP status; `request_id` echo (null pre-identity, `zs-` post-identity); chat provider deltas byte-identical to `req.text_deltas`; embeddings deltas item-for-item identical in count and order; the same output bytes.
    - For `scope: both` plans, assert that input and output decisions equal today's decisions for the same findings.
  - Run on the UNFIXED tree. **EXPECTED OUTCOME**: PASS.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.12, 3.13, 3.16_
  - _Design: Property 2, Property 14_

- [x] 12. Confirm the admission, budget and lifecycle preservation baseline
  - **Property 2: Preservation** - Shed, drain and lease-outage semantics unchanged
  - **IMPORTANT**: Reuse the shipped suites unchanged as the preservation oracle. Run them on the UNFIXED tree and record them: `test_lgw19_*`, `test_lgw06_*`, `test_lgw15_*`, GW13 and GW16 (including `test_gw16_embeddings_block.py`).
  - Observe and record:
    - shed verdict, jittered Retry-After ≥ 1 s and `x-should-retry: false`
    - the SIGTERM drain state machine
    - lease outage: spend the remaining lease, then `budget_unavailable`, never `shared_state_unavailable`
    - no request-path store I/O, async refill at the low watermark, no worker multiplication
  - **EXPECTED OUTCOME**: PASS.
  - Note: the `_in_flight` check in LGW19 Property 1 and the dev-profile tests encode the defect. They are reconciled in tasks 23.4 and 27.4, not here.
  - _Requirements: 3.10, 3.11, 3.12_
  - _Design: Property 2, Property 14_

- [-] 13. Checkpoint: exploration baseline
  - Run gate G. Expected: 1948 passed (plus the new preservation tests and the exploratory cases that already hold), 95 skipped, 2 xfailed plus the new strict-xfail exploratory cases, 0 failed.
  - Record the counterexamples from tasks 2–9 in the evidence directory. If any expected counterexample does NOT reproduce, stop and re-hypothesise that group's root cause before fixing (design "Expected counterexamples").
  - Commit: `test(v2-leaks): exploratory bug-condition + preservation baselines (unfixed tree)`.
  - _Requirements: 3.14, 3.15_

### Phase 1 — domain

- [ ] 14. Domain layer (Group A)

  - [~] 14.1 Add `FindingDisposition.finding_index`
    - Add `finding_index: int | None = None` to `domain/decision.py::FindingDisposition`. It indexes `Decision.findings`, and the default keeps every existing construction compiling.
    - _Bug_Condition: C_A (obligations must be keyed per finding)_
    - _Expected_Behavior: Property 3, Property 4_
    - _Preservation: Property 14 (existing decisions are unchanged apart from the new field)_
    - _Requirements: 2.1, 2.2_

  - [~] 14.2 Layer checkpoint and commit
    - Run gate G (no new failures; `check_frozen_dataclasses` clean).
    - Commit: `feat(gw2-domain): FindingDisposition.finding_index (v2-enforcement-leaks A)`.
    - _Requirements: 3.14_

### Phase 2 — runtime

- [ ] 15. Runtime layer (Group F)

  - [~] 15.1 Declare the token estimate in `runtime/resources.py`
    - Add `_CHARS_PER_TOKEN_DIVISOR = 4`.
    - Add the `@staticmethod ResourceContract.estimate_request_tokens(chars, max_tokens, n)`:
      - `input = max(1, chars // 4)` if `chars > 0`, else `0`
      - returns `max(input + max_tokens * n, 1)`
      - raises `ValueError` on `chars < 0`, `max_tokens < 0` or `n < 1`
    - _Bug_Condition: C_F (cost unit mismatch)_
    - _Expected_Behavior: Property 12 (v1 estimate)_
    - _Requirements: 2.15_

  - [~] 15.2 Declare the four v1 output-budget constants
    - Constants and `@staticmethod` accessors:
      - `_OUTPUT_TOKENS_HARD_CAP = 65536` → `max_output_tokens()`
      - `_DEFAULT_RESPONSE_TOKEN_CEILING = 4096` → `default_response_token_ceiling()`
      - `_MAX_OUTPUT_TOKENS_BOUNDARY = 1_000_000` → `max_output_tokens_boundary()`
      - `_MAX_COMPLETIONS_PER_REQUEST = 1` → `max_completions()`
    - Add the import-time ordering check, raising `CapacityUnavailable` unless `1 ≤ default ceiling ≤ hard cap ≤ boundary` and `max_completions() ≥ 1`.
    - _Expected_Behavior: Property 16_
    - _Requirements: 2.15, 2.16_

  - [~] 15.3 Add `max_request_tokens()`
    - `estimate_request_tokens(max_request_bytes(), max_output_tokens(), max_completions())`, which is `max(1, max_request_bytes() // 4) + 65536`.
    - _Expected_Behavior: Property 12, Property 16 (upper bound on every parsed request's cost)_
    - _Requirements: 2.15, 2.16_

  - [~] 15.4 Add `store_keys.budget_workers(org)`
    - A ZSET key under the `{rv2}` hash tag, scored by store-clock lease expiry.
    - _Bug_Condition: C_F (the chunk ignores active workers)_
    - _Expected_Behavior: Property 12 (fair-share grant)_
    - _Requirements: 2.15_

  - [~] 15.5 Runtime unit tests
    - `tests/runtime/test_v2_leaks_token_contract.py`:
      - `estimate_request_tokens`: `(0,0,1)==1`, `(3,0,1)==1`, `(8,0,1)==2`, `(2000,256,2)==1012`, `(0,100,3)==300`; negative arguments and `n=0` raise
      - `_CHARS_PER_TOKEN_DIVISOR == 4`
      - the constant values (`65536`, `4096`, `1_000_000`, `1`) and the ordering check
      - `max_request_tokens() == max(1, max_request_bytes() // 4) + 65536`, e.g. `1_407_713` at `per_worker_rss = 2 GiB`
      - `budget_workers` key shape and hash tag
    - _Requirements: 2.15_

  - [~] 15.6 Layer checkpoint and commit
    - Run gate G. `check_capacity_literals` must pass with the new literals only in `runtime/resources.py`.
    - Commit: `feat(gw2-runtime): token estimate + v1 output-budget contract + budget_workers key (v2-enforcement-leaks F)`.
    - _Requirements: 3.14_

### Phase 3 — dispatch

- [ ] 16. Dispatch layer (Group A)

  - [~] 16.1 Key verify obligations by finding index
    - In `dispatch/verify.py::_spans_to_verify`: when every `per_finding` carries `finding_index`, oblige exactly those findings' spans. Otherwise fall back to the shipped detector-keyed rule (`MinimalOutputResolver`).
    - _Bug_Condition: C_A (mixed SECRET→flag / CREDENTIAL→redact false failure)_
    - _Expected_Behavior: Property 3, Property 4_
    - _Requirements: 2.1, 2.2_

  - [~] 16.2 Fix `_resplit` boundaries
    - In `_resplit(sanitized, request, transformations)`, shift each original delta boundary by the summed length delta of the transformations that end at or before it.
    - A straddling transformation places its replacement in the delta where it starts, and the later delta loses the covered prefix.
    - Raw bytes never cross channels; each delta holds only its own sanitized bytes.
    - _Bug_Condition: C_A (delta 1's first byte moves into delta 0)_
    - _Expected_Behavior: Property 3, Property 15 (embeddings items stay independent)_
    - _Preservation: no transformations → identical deltas (3.2)_
    - _Requirements: 2.1, 2.3, 2.20_

  - [~] 16.3 Extract `verified_upstream`
    - In `dispatch/authorized.py`, extract steps (1) `assert_authorized`, (2) `apply_and_verify` and (4) `Transform.to_provider` into the public `verified_upstream(auth, *, decision, request, transform) -> UpstreamRequest`.
    - Refactor `AuthorizedProviderEntry.dispatch()` onto it so there is one code path. `VerificationFailure` propagates.
    - _Expected_Behavior: Property 1 (A: the provider receives only verified bytes)_
    - _Requirements: 2.1, 2.2, 2.19, 2.21_

  - [~] 16.4 Dispatch unit tests and exploration flip
    - Add to `tests/dispatch/test_v2_leaks_verify.py`:
      - `_resplit` with growth, shrink, straddling spans and empty deltas
      - `verified_upstream` raises on a forged authorization
      - the BLOCK path is unreachable because `authorize` returns `None`
    - Remove the strict-xfail markers from task 4's cases 1 and 2, which now pass.
    - _Requirements: 2.1, 2.2, 2.3_
    - _Design: Property 3, Property 4_

  - [~] 16.5 Layer checkpoint and commit
    - Run gate G (`check_dispatch_provenance` clean; shipped `dispatch` tests unchanged).
    - Commit: `fix(gw2-dispatch): per-delta resplit, finding-keyed obligations, verified_upstream (v2-enforcement-leaks A)`.
    - _Requirements: 3.14_

### Phase 4 — resolve

- [ ] 17. Resolve layer (Groups A, E)

  - [~] 17.1 Set `finding_index` and coalesce REDACT spans
    - In `resolve/resolver.py` (pure, imports only domain):
      - set `finding_index` on every per-finding disposition
      - normalise REDACT transformations: sort by span, then coalesce overlapping or identical spans that share a replacement into their union
      - never merge spans that only touch (`a.end == b.start`)
    - _Bug_Condition: C_A, with C_B/C_C now emitting overlapping findings_
    - _Expected_Behavior: Property 3, Property 15 (no fusion across delta or item boundaries)_
    - _Preservation: non-overlapping transformations give the same `Decision` apart from `finding_index` (3.4, 3.5)_
    - _Requirements: 2.1, 2.3, 2.20, 3.4, 3.5_

  - [~] 17.2 Resolver tests
    - `tests/resolve/test_v2_leaks_resolver.py`:
      - coalescing unit cases; touching spans across a delta boundary are never merged
      - a 10,000-iteration seeded property: for non-overlapping transformations, the `Decision` equals the pre-fix decision apart from `finding_index`; the C22 "adding a REDACT rule never removes a BLOCK" property still holds
      - **Property 11** (resolver half), 10,000 seeded iterations: for random rule scopes, findings and phases, `resolve(findings, plan, phase)` selects only rules scoped `phase` or `both`
    - _Requirements: 2.13, 3.4, 3.5_
    - _Design: Property 11, Property 14_

  - [~] 17.3 Layer checkpoint and commit
    - Run gate G.
    - Commit: `fix(gw2-resolve): finding_index + REDACT span coalescing (v2-enforcement-leaks A)`.
    - _Requirements: 3.14_

### Phase 5 — detect

- [ ] 18. Detect layer (Groups B, C)

  - [~] 18.1 Add the versioned confusable set
    - New `detect/confusables.py`:
      - `CONFUSABLE_SET_VERSION = "amf-confusables-1"`
      - `CONFUSABLES: MappingProxyType[str, str]`, the Cyrillic and Greek upper- and lower-case letters whose UTS #39 prototype is a single ASCII letter, each verified against the pinned `confusables.txt`
      - `CONFUSABLES_DIGEST`, a sha256 over the sorted items
    - The set is a superset of both current tables. It adds at least Greek Β Ζ Η Ι Κ Μ Ν Ρ Τ Υ Χ ι ν ρ υ and Cyrillic В К М Н Т У Ѕ І Ј Ӏ ѕ ԛ ԝ ӏ.
    - _Bug_Condition: C_C (confusable outside `_HOMOGLYPHS`)_
    - _Expected_Behavior: Property 7, Property 8_
    - _Requirements: 2.8_

  - [~] 18.2 Fold through the one table
    - `detect/deterministic/canonicalize.py::_fold_homoglyphs` reads `CONFUSABLES`. The fold stays 1:1 per character, so span records are unchanged.
    - `detect/semantic/language.py`: `CONFUSABLE_FOLD = CONFUSABLES`, a re-export, so GW09 and GW10 share one table.
    - _Preservation: confusable-free text has an identity span map (3.8)_
    - _Requirements: 2.8, 3.8_

  - [~] 18.3 Pattern changes in `detect/deterministic/patterns.py`
    - Add `PatternSpec.validator: str | None` and set `credential_card.validator = "luhn"`.
    - Bound the unbounded prefilters:
      - email local part `{1,64}`
      - email domain `{1,253}`
      - phone `{9,14}`
      - AWS key `{10,16}`
      - API-key value `{12,512}`
    - Add lead-context jailbreak alternatives before the existing ones: `you\s+are\s+DAN\b`, `enable\s+developer\s+mode`, `answer\s+without\s+(?:any\s+)?restrictions`. Keep the existing alternatives.
    - Audit every pattern for the start-superset invariant. Leave the phone start unchanged (no leading `(`).
    - `ruleset_version()` serialises the validator name and appends `("confusables", CONFUSABLE_SET_VERSION, CONFUSABLES_DIGEST)`.
    - _Bug_Condition: C_B (no Luhn), C_C (prefilter not a start-superset)_
    - _Expected_Behavior: Property 5, Property 7, Property 8_
    - _Requirements: 2.4, 2.5, 2.7, 2.8_

  - [~] 18.4 Matcher validator and failed-region rescan
    - In `detect/deterministic/matcher.py`, keep the shipped `_COMBINED.finditer` as the primary pass (`_COMBINED.groups == len(RULESET)` still holds).
    - Validator: apply the pattern's validator after a confirm, from `MappingProxyType({"luhn": _luhn_ok})`.
    - Rescan, on a failed confirm or validator over `R = [hit.start, hit.end)`:
      - evaluate every other pattern leftmost-first inside `R`, and the failed pattern at positions after `hit.start`
      - a nested failure recurses with the failed set excluded
      - single-exclusion combined regexes are precompiled at import into a `MappingProxyType`
      - deeper sets use the once-compiled `_ALL_AT` per-position lookahead, restricted to the nested region
      - confirms use `precise.match(text, q)` on the full text, never `endpos`
    - Per-pattern cursors prevent duplicate or overlapping same-pattern emission.
    - Output is sorted by `(start, RULESET order)`.
    - _Bug_Condition: C_B (leftmost-first consumption)_
    - _Expected_Behavior: Property 5, Property 6_
    - _Preservation: one canonicalisation pass and one primary match per channel value, no per-pattern loop (3.9); Property 13_
    - _Requirements: 2.4, 2.5, 2.6, 3.9_

  - [~] 18.5 Reconcile the shipped GW09 tests that encode the defect
    - `tests/detect/test_deterministic_detectors_gw09.py::test_property6_*`: the reference scan adds the region-rescan and Luhn semantics. Do not weaken the property.
    - `…::test_property7_*`: `_hash_model` appends the confusables record and the validator field.
    - `tests/detect/fixtures_gw09.py`: update the card comment ("NOT emitted by scan" is now false); credential recall is 1.0.
    - _Requirements: 3.6, 3.15_

  - [~] 18.6 Layer checkpoint and commit
    - Run gate G (`check_no_module_mutable` clean for the new `MappingProxyType` tables).
    - Commit: `fix(gw2-detect): failed-region rescan + Luhn + bounded prefilters + versioned confusables (v2-enforcement-leaks B/C)`.
    - _Requirements: 3.14_

- [ ] 19. Group B fix-checking and preservation

  - [~] 19.1 Write the card-detection property test
    - **Property 5: Bug Condition (B)** - Cards detected exactly when Luhn-valid
    - `tests/detect/test_v2_leaks_matcher_properties.py`, 10,000 seeded runs over random 13–19-digit PANs (Luhn valid and invalid, plus 12- and 20-digit runs) with no separators, single spaces or single dashes, embedded in random text.
    - Assert a `credential_card` CREDENTIAL finding over the whole PAN iff the digits pass Luhn and the length is in range.
    - Unit vectors: the Luhn validator, the separator grammar, the 13- and 19-digit bounds.
    - _Requirements: 2.4, 2.5_

  - [~] 19.2 Write the order-independence property test
    - **Property 6: Bug Condition (B)** - Order independence over failed regions
    - 10,000 seeded texts. Every match a single pattern confirms alone (single-pattern reference scan) appears in `scan(text)`, or lies inside a confirmed match of another pattern.
    - Structural cost check: regex invocations ≤ `c·len(text)` on adversarial corpora (digit runs, `x` runs, `a@` runs) at the decode-budget size.
    - Record `scan("x"*262_144)` wall-clock (63.1 s unfixed) in the evidence, not in an assertion.
    - _Requirements: 2.6, 3.9_

  - [~] 19.3 Verify the Group B exploration test now passes
    - **Property 1: Expected Behavior** - Card numbers detected
    - **IMPORTANT**: Re-run the SAME test from task 5 (section B). Do NOT write a new test.
    - Remove its strict-xfail markers. **EXPECTED OUTCOME**: PASS.
    - _Requirements: 2.4, 2.5, 2.6_

  - [~] 19.4 Verify detection preservation still holds
    - **Property 2: Preservation** - Detection monotone (design Property 13)
    - **IMPORTANT**: Re-run the SAME tests from task 10, plus LGW09-1 and LGW09-6 (zero terminal blocks; dev-traffic FPR 0.0). Do NOT write new tests.
    - **EXPECTED OUTCOME**: PASS.
    - _Requirements: 3.6, 3.7, 3.8, 3.9_

- [~] 20. Checkpoint: Group B closed
  - Run gate G and confirm the xfail count dropped by exactly the Group B markers.
  - Ask the user if questions arise.
  - _Requirements: 3.14, 3.15_

- [ ] 21. Group C fix-checking and preservation

  - [~] 21.1 Write the phrase-and-confusable coverage property test
    - **Property 7: Bug Condition (C)** - Phrase and confusable coverage
    - 10,000 seeded spellings of `you are DAN`, `enable developer mode`, `answer without (any) restrictions` and the override phrases, with any subset of letters replaced by declared confusables.
    - Assert the EXECUTED finding, and that redacting its span in the original text removes every confusable-bearing byte.
    - Start-superset unit test: for each precise alternative, a generated match string is matched by the prefilter at the same start.
    - _Requirements: 2.7, 2.8_

  - [~] 21.2 Write the versioned-confusable property test
    - **Property 8: Bug Condition (C)** - Versioned confusable set
    - A mutation of the table changes `detector_version`.
    - For every table character, `canonicalize` folds it to its declared Latin letter with a 1:1 span record.
    - `CONFUSABLE_FOLD is CONFUSABLES`.
    - _Requirements: 2.8_

  - [~] 21.3 Verify the Group C exploration test now passes
    - **Property 1: Expected Behavior** - Jailbreak phrases and confusables detected
    - **IMPORTANT**: Re-run the SAME test from task 6 (section C). Do NOT write a new test.
    - Remove its strict-xfail markers. **EXPECTED OUTCOME**: PASS.
    - _Requirements: 2.7, 2.8_

  - [~] 21.4 Verify detection preservation still holds
    - **Property 2: Preservation** - Detection monotone (design Property 13)
    - **IMPORTANT**: Re-run the SAME tests from task 10 and LGW09-1/LGW09-6 with the larger confusable set. Do NOT write new tests.
    - **EXPECTED OUTCOME**: PASS.
    - _Requirements: 3.6, 3.7, 3.8_

- [~] 22. Checkpoint: Group C closed
  - Run gate G and confirm the xfail count dropped by exactly the Group C markers.
  - Ask the user if questions arise.
  - _Requirements: 3.14, 3.15_

### Phase 6 — admit

- [ ] 23. Admit layer (Groups D, F)

  - [~] 23.1 Add `BoundedQueue.remove`
    - `admit/queues.py::BoundedQueue.remove(item) -> bool` removes one occurrence plus its aligned `_enqueued_at`. It is O(depth), and depth is contract-bounded.
    - _Bug_Condition: C_D (`pop()` removes the FIFO head)_
    - _Expected_Behavior: Property 10_
    - _Requirements: 2.11_

  - [~] 23.2 Make `complete` idempotent, slot-exact and pruning
    - In `admit/admission.py`:
      - `complete(request_id)` returns without touching any queue for an unknown id
      - otherwise it removes exactly that id's slot, decrements its admit refcount, prunes the in-flight record at 0 and publishes the queue
      - `_decide` increments the refcount on admit (content-hashed ids can collide)
      - `register_stream` and `finish_stream` are unchanged
    - _Bug_Condition: C_D_
    - _Expected_Behavior: Property 10_
    - _Preservation: shed verdicts and the drain state machine are unchanged (3.10)_
    - _Requirements: 2.11, 3.10_

  - [~] 23.3 Lease fair share, overdraft and refund
    - `admit/lease.py::acquire`: inside the existing WATCH/MULTI, read `TIME`, `ZREMRANGEBYSCORE(budget_workers, -inf, now)` and `ZCARD`. Compute `active = max(1, card + (self not present))` and `grant = min(pool, cap, max(1, pool // active))`. `ZADD` this worker with expiry `now + ttl` in the same MULTI.
    - `return_unspent` issues `ZREM`; a crashed worker drops out at TTL.
    - `BudgetLease(overdraft_tokens: int | None = None)`, where `None` means 0:
      - `try_spend` spends as today when `remaining ≥ cost`
      - when `remaining > 0` and `cost - remaining < overdraft_tokens`, it spends, lets `remaining` go negative and schedules a refill
      - otherwise it refuses as today, spending nothing
      - the overshoot is published on `amf_quota_lease_overshoot`
    - `refund(cost, generation)` does `remaining += cost` iff `generation == state.generation`. It is local, with no store I/O.
    - _Bug_Condition: C_F (the chunk ignores remaining / active; no refund)_
    - _Expected_Behavior: Property 12_
    - _Preservation: outage semantics, `budget_unavailable` only, no request-path store I/O, async refill, no worker multiplication (3.11); `check_tenant_scale` passes_
    - _Requirements: 2.15, 2.16, 2.17, 3.11_

  - [~] 23.4 Add `derive_lease_cap_tokens` and `QuotaComponent.refund`
    - `admit/quota.py`:
      - `derive_lease_chunk` is unchanged; correct its docstring to say it is the request-count in-flight bound
      - new pure `derive_lease_cap_tokens(contract, *, q_safe) = derive_lease_chunk(...) × contract.max_request_tokens()`
      - `QuotaComponent.refund(cost, *, request_id)` delegates to the lease
    - Reconcile `tests/admit/test_lgw19_admission.py` (Property 1 loop): `_in_flight[rid].finished is True` becomes `rid not in _in_flight` (pruned). Do not weaken any other assertion.
    - _Expected_Behavior: Property 10, Property 12_
    - _Requirements: 2.11, 2.15, 2.17_

  - [~] 23.5 Write the exact-`complete` property test
    - **Property 10: Bug Condition (D)** - Idempotent, exact, pruning `complete`
    - `tests/admit/test_v2_leaks_admission_properties.py`, 10,000 seeded interleavings of `admit` and `complete`, with unknown, repeated and colliding ids.
    - Assert that `complete` releases at most one slot per prior unmatched admit of that id and none otherwise, and that `len(_in_flight) ≤` the admitted, unfinished ids after every step.
    - Unit tests: `BoundedQueue.remove` keeps the depth and age deques aligned; `complete` with unknown, repeated and colliding ids.
    - Remove the strict-xfail markers from the `tests/admit/test_v2_leaks_admission.py` cases (task 7).
    - _Requirements: 2.11_

  - [~] 23.6 Write the token-lease conservation property test (ledger half)
    - **Property 12: Bug Condition (F)** - Token lease conservation
    - `tests/admit/test_v2_leaks_lease_properties.py`: N leases over one fakeredis, 10,000 seeded sequences of random costs, spends, refunds, crashes (TTL reclaim) and returns. Assert:
      - every grant is at most `min(pool, cap, max(1, pool // active_workers))`
      - admitted tokens minus refunds are at most `limit + Σ_workers overdraft_bound`
      - no cost at most `max_request_tokens()` is refused while the lease holds budget
      - an uncoverable cost is refused `budget_unavailable` with nothing spent
      - a refund restores exactly the cost within the same generation and is ignored across generations
    - Remove the strict-xfail markers from the lease-level cases in `tests/admit/test_v2_leaks_budget.py` (the 4-worker grant).
    - _Requirements: 2.15, 2.16, 2.17_

  - [~] 23.7 Layer checkpoint and commit
    - Run gate G. `test_lgw19_*` and `test_lgw06_*` must pass with only the 23.4 reconciliation. `check_tenant_scale` and `check_capacity_literals` must be clean.
    - Commit: `fix(gw2-admit): slot-exact pruning complete + fair-share token lease, overdraft, refund (v2-enforcement-leaks D/F)`.
    - _Requirements: 3.10, 3.11, 3.14_

### Phase 7 — edge

Each edge sub-step must keep `check_sizes` green. `edge/openai_chat.py` must end ≤ 800 lines and `edge/openai_misc.py` ≤ 799 lines, so new logic goes into the new sibling modules.

- [ ] 24. Edge wire layer (Groups A, F)

  - [~] 24.1 Changes in `edge/wire/openai.py`
    - Message index (A):
      - `fold_input_channels` also returns each delta's message index
      - `CanonicalRequest.input_message_index: tuple[int, ...] = ()`
      - `_assert_fold_total` unchanged
    - Output budget (F):
      - `CanonicalRequest` gains `max_tokens: int = 0`, `n: int = 1`, `max_completion_tokens: int | None = None` and `request_clamps: tuple[str, ...] = ()`
      - declare `INVALID_MAX_TOKENS` and `INVALID_MAX_COMPLETION_TOKENS`
      - `MalformedRequest` gains keyword-only `param: str | None = None`
    - Parser: `OpenAIRequestParser.parse` reads all three fields through one `_int_field(obj, key, *, default, minimum, maximum=None, over_code=None)` helper:
      - absent or JSON `null` gives the default
      - a bool, float, string, negative value or `n=0` raises `MALFORMED_WRONG_SHAPE`
      - a token field above `max_output_tokens_boundary()` raises `MalformedRequest(over_code, param=key)`
      - `max_tokens = 0` stays 0
    - New pure `clamp_output_budget(req, *, org_ceiling)`:
      - `n' = min(n, max_completions())`
      - `max_tokens' = min(max_tokens, min(org_ceiling, max_output_tokens()))`
      - one `"<field>=<pre>-><post>"` record per changed field, in v1's order (`n` first, then `max_tokens`)
      - idempotent
      - `org_ceiling < 1` raises `ValueError`
      - `max_completion_tokens` is untouched
    - New pure `prompt_chars(req)`: the summed `input_channels` text length, plus one separator for each additional content part of the same message.
    - _Bug_Condition: C_A (content parts joined per message), C_F (output budget unread and unbounded)_
    - _Expected_Behavior: Property 3, Property 12, Property 16_
    - _Preservation: parse codes and `request_id: null` rendering unchanged (3.12)_
    - _Requirements: 2.3, 2.15, 2.16, 3.12_

  - [~] 24.2 `MiscEmbeddingsRequest.input_is_string`
    - In `edge/wire/openai_misc.py`, add `input_is_string: bool = False`. `parse_embeddings` sets it when the JSON `input` is a string.
    - _Expected_Behavior: Property 15 (shape clause)_
    - _Requirements: 2.20_

  - [~] 24.3 Wire unit tests
    - Parser grammar:
      - absent and `null` give `0`, `None` and `1`
      - off-contract values raise `MALFORMED_WRONG_SHAPE`
      - `max_tokens = 1_000_000` is accepted
      - `1_000_001` raises `invalid_max_tokens` with `param == "max_tokens"`
      - `max_completion_tokens = 1_000_001` raises `invalid_max_completion_tokens` with `param == "max_completion_tokens"`
    - `clamp_output_budget` with `org_ceiling = 4096`:
      - `max_tokens` 0→0, 100→100, 4096→4096, and 1_000_000→4096 recorded `"max_tokens=1000000->4096"`
      - `org_ceiling = 100_000` with `max_tokens = 1_000_000` → 65536
      - `n` 1→1 with no record; 8→1 recorded `"n=8->1"`
      - applying it twice equals applying it once
      - `org_ceiling = 0` raises
    - `prompt_chars` joins the parts of one message with one separator.
    - `parse_embeddings` sets `input_is_string` for a string input and not for a one-element list.
    - _Requirements: 2.15, 2.20_

- [ ] 25. Edge protocols, holds, phase resolvers and input enforcement (Groups A, D, E)

  - [~] 25.1 Extend the `chat_deps` protocols and reconcile the test fakes
    - In `edge/chat_deps.py`:
      - `AdmissionDecider.complete(request_id) -> None`
      - `BudgetDecider.refund(cost, *, request_id) -> None`
      - the fail-closed defaults implement both as no-ops
      - the real `AdmissionController` and `QuotaComponent` satisfy them structurally
    - Add no-op `complete` and `refund` to the 21 admission/budget test doubles across 11 edge test files, including the `test_gw16_embeddings_*` fakes. This is a protocol extension, not a weakening; no assertion changes.
    - _Preservation: default boot unchanged (3.13)_
    - _Requirements: 2.9, 2.17, 3.13_

  - [~] 25.2 Add `edge/request_holds.py`
    - `RequestHolds` is a `__slots__` class, per request and never shared. It records `admitted(request_id)`, `spent(cost, request_id)` and `mark_dispatched()`.
    - `release(admission, quota)` runs once behind a latch:
      - if spent and not dispatched, `quota.refund(cost, request_id=...)`
      - if admitted, `admission.complete(request_id)`
      - synchronous, never awaits, never raises
    - _Bug_Condition: C_D, C_F (refund when nothing dispatched)_
    - _Expected_Behavior: Property 9, Property 12_
    - _Requirements: 2.9, 2.10, 2.17_

  - [~] 25.3 Add `edge/phase_resolvers.py`
    - `PhaseResolverFactory = Callable[[ExecutionPlan, Phase], OutputResolver]`.
    - `resolver_for(route, plan, phase)` returns `route.phase_resolvers(plan, phase)` when set, else `route.resolver`.
    - _Bug_Condition: C_E_
    - _Expected_Behavior: Property 11_
    - _Preservation: the unset branch reproduces today's behaviour, including the default boot (3.5, 3.13)_
    - _Requirements: 2.13, 3.5, 3.13_

  - [~] 25.4 Add `edge/input_enforcement.py`
    - `scan_deltas(text_deltas, *, join_runs, detector, resolver, on_invoke=None) -> InputScan`:
      - one detector run per delta, plus one per joined `(first, last)` run
      - every span shifted by its channel base offset
      - exact duplicates dropped, then one resolve
      - a detector or resolver raise maps to `scan_failure`
    - `scan_input(req, detector, resolver)` derives `join_runs` from `req.input_message_index` (maximal runs of consecutive `content_part_text` deltas of the same message).
    - `stage_upstream(decision, request, transform)`:
      - `authorize(decision)`; `None` → `input_blocked`
      - then `verified_upstream(...)`
      - `VerificationFailure` → `REFUSE_INPUT_UNMASKABLE`
    - _Bug_Condition: C_A_
    - _Expected_Behavior: Property 3, Property 4_
    - _Preservation: no transformations → byte-identical deltas (3.2); the provenance gate is untouched (3.14)_
    - _Requirements: 2.1, 2.2, 2.3, 3.2_

  - [~] 25.5 Add `edge/embeddings_input.py`
    - `embeddings_deltas(req)` gives one `("embeddings_input", item)` per item, in order. It moves `EmbeddingsSurface._input_as_deltas` out of `openai_misc.py`.
    - `scan_embeddings(req, *, detector, resolver, on_invoke)` calls `scan_deltas(..., join_runs=())`.
    - `stage_embeddings(decision, req, *, destination, transform)`:
      - calls `stage_upstream(...)`
      - then checks the shape: the delta count equals `len(req.input_items)`, every channel is `embeddings_input`, and `input_is_string` implies exactly one delta
      - a shape mismatch or `VerificationFailure` returns `input_unmaskable`
    - _Bug_Condition: C_A (embeddings)_
    - _Expected_Behavior: Property 4, Property 15_
    - _Preservation: clean items byte-identical, same count and order (3.16)_
    - _Requirements: 2.19, 2.20, 2.21, 3.16_

  - [~] 25.6 Unit tests for the new edge modules
    - `RequestHolds.release` is idempotent and never raises.
    - `resolver_for`: the unset factory returns `route.resolver`.
    - `scan_deltas`: base-offset shifting, join-run findings and de-duplication.
    - `stage_embeddings`: a delta-count mismatch, a `VerificationFailure` and `input_is_string` with more than one delta each return `input_unmaskable`.
    - _Requirements: 2.2, 2.9, 2.13, 2.21_

- [ ] 26. Edge surface wiring (Groups A, D, E, F)

  - [~] 26.1 Wire `edge/openai_chat.py`
    - `_input_scan` delegates to `scan_input` and returns `(refuse_code, decision)`, resolving at `Phase.INPUT` via `resolver_for`.
    - `_proceed(plan, req, decision, request_id)` builds the body only from `stage_upstream(decision, GatewayRequest(destination, req.model, req.text_deltas), transform)`. Streaming and non-streaming share it.
    - Declare `REFUSE_INPUT_UNMASKABLE = "input_unmaskable"`.
    - After the plan is pinned and before the budget phase, `req = clamp_output_budget(req, org_ceiling=ResourceContract.default_response_token_ceiling())`, one call line.
    - `_estimated_cost` returns `ResourceContract.estimate_request_tokens(prompt_chars(req), req.max_tokens, req.n)`.
    - Call `holds.admitted` after `Admitted` and `holds.spent` after the budget phase.
    - The parse-refusal render site passes `param=exc.param`.
    - The module ends ≤ 800 lines.
    - _Bug_Condition: C_A, C_D, C_E, C_F_
    - _Expected_Behavior: Property 3, Property 4, Property 9, Property 11, Property 12, Property 16_
    - _Preservation: Property 14 (ALLOW/FLAG deltas identical; BLOCK → `input_blocked`, zero calls)_
    - _Requirements: 2.1, 2.2, 2.3, 2.9, 2.13, 2.15, 2.16, 2.17, 3.1, 3.2_

  - [~] 26.2 Wire `edge/routes.py`, `edge/openai_nonstream.py` and `edge/error_frame_scan.py`
    - Add `ChatRoute.phase_resolvers` / `MiscRoute.phase_resolvers: PhaseResolverFactory | None = None`.
    - `ChatRoute.handle`: `holds = RequestHolds()`; `try: run_lifecycle(scope, body, holds) … finally: holds.release(self.admission, self.quota)`.
    - Call `holds.mark_dispatched()` immediately before both `provider.open` sites (`_next_attempt._attempt` and `openai_nonstream._collect_output_channels`, passed through).
    - `_build_pipeline(plan)`, `_ErrorFrameScan` and `_enforce_channels` resolve at `Phase.OUTPUT` with the request's pinned plan.
    - _Bug_Condition: C_D, C_E_
    - _Expected_Behavior: Property 9, Property 11_
    - _Preservation: output guard masking unchanged for `scope: both` (3.3, 3.5); drain unchanged (3.10)_
    - _Requirements: 2.9, 2.10, 2.12, 2.13, 3.3, 3.10_

  - [~] 26.3 Wire `edge/openai_misc.py` (`EmbeddingsSurface`)
    - Delete `_input_scan` and `_input_as_deltas`, which moved to `embeddings_input.py`.
    - `handle` runs `scan_embeddings(...)` with `resolver_for(deps, plan, Phase.INPUT)`:
      - BLOCK → `input_blocked`; a raise → `scan_failure` (both unchanged)
      - otherwise `stage_embeddings(...)`
      - a refuse code renders through `_refuse` with `exports.record_withheld()`
    - `_collect_and_render(req, upstream, send, ...)` takes the staged `UpstreamRequest`.
    - Holds pattern:
      - `holds.admitted` after `Admitted`; `holds.spent` after budget
      - `mark_dispatched()` immediately before `_drain_vectors` opens the provider
      - `release` in a `finally`
      - refund on `input_blocked`, `scan_failure` and `input_unmaskable`
    - `_estimated_cost` returns `estimate_request_tokens(sum(len(i) for i in req.input_items), 0, 1)`.
    - Moderations is unchanged.
    - The module ends ≤ 799 lines.
    - _Bug_Condition: C_A (embeddings), C_D, C_E, C_F_
    - _Expected_Behavior: Property 4, Property 9, Property 11, Property 12, Property 15_
    - _Preservation: Property 14 (embeddings items byte-identical; BLOCK zero calls) (3.16)_
    - _Requirements: 2.9, 2.13, 2.15, 2.17, 2.19, 2.20, 2.21, 3.16_

  - [~] 26.4 Error mappings in `edge/errors.py`
    - Map `input_unmaskable` to `(400, "invalid_request_error")`, directly after `input_blocked`, under the extended GW15/GW16 input-content-block comment. The comment cites GW01 §10.2.2 and v1's `_build_safe_block_response` → `GATEWAY_BLOCK_STATUS`, default 400.
    - Map `invalid_max_tokens` and `invalid_max_completion_tokens` to `(400, "invalid_request_error")` as literal-string keys. The comment cites v1's boundary validator.
    - Each gets a declared `Error_Frame` message with the boundary rendered from `ResourceContract.max_output_tokens_boundary()`, not a literal.
    - Update the "Status choices" comment block: `output_blocked` and `scan_failure` stay 403; `input_blocked` and `input_unmaskable` are 400.
    - _Expected_Behavior: Property 4, Property 16_
    - _Requirements: 2.2, 2.21_

  - [~] 26.5 Lease wiring
    - The edge wiring constructs `BudgetLease(..., overdraft_tokens=contract.max_request_tokens())` and the cap from `derive_lease_cap_tokens`.
    - _Expected_Behavior: Property 12_
    - _Requirements: 2.16_

  - [~] 26.6 Flip the shipped xfail and the edge exploration markers
    - Remove the strict `xfail` marker from `tests/edge/test_dev_profile_pipeline.py::test_input_redact_masks_pii_before_the_provider`. It must pass unchanged otherwise.
    - Remove the strict-xfail markers from tasks 2, 3, 7 (lifecycle), 8 (programmatic half) and 9 (lifecycle cases) wherever they now pass. Markers that still depend on the dev cleanup (dev boot accepting a non-`both` plan, dev token charging, `DevServedApp` removal) stay until task 27.
    - _Requirements: 2.1, 3.15_

  - [~] 26.7 Edge checkpoint
    - Run gate G. `check_sizes` must be clean with both surface modules at or below their recorded line counts. `lint-imports` must be clean (`egress` never imports `detect`). `check_http_outside_edge_resolve` and `check_dispatch_provenance` must be clean.
    - _Requirements: 3.14_

- [ ] 27. Dev-profile cleanup (Groups D, E, F)

  - [~] 27.1 Remove the slot workaround in `edge/dev_admit.py`
    - Delete `DevCompletingAdmission`'s per-request bucket and `DevServedApp`'s per-request `begin/end`. Pass the bare `AdmissionController`.
    - `DevServedApp` keeps only the lifespan lease startup and shutdown.
    - _Bug_Condition: C_D (wrapper outside the lifecycle)_
    - _Expected_Behavior: Property 9 (one lifecycle on both boot paths)_
    - _Requirements: 2.12_

  - [~] 27.2 Enable phase-scoped plans in `edge/dev_components.py`
    - Delete `require_phase_agnostic`'s scope check, keeping the "no active rules" refusal.
    - Replace `plan_resolver(plan)` with `phase_resolver_factory()`, which returns `lambda plan, phase: PlanResolver(plan=plan, phase=phase)`.
    - In `edge/dev_profile.py`, wire `phase_resolvers` on both routes and update the resolver boot line.
    - _Bug_Condition: C_E_
    - _Expected_Behavior: Property 11_
    - _Requirements: 2.14_

  - [~] 27.3 Charge real token costs in the dev profile
    - `DevLeaseBudget.evaluate` charges the real clamped token cost. Delete `_DEV_COST_PER_REQUEST`.
    - `DevLeaseBudget.refund` delegates; `DevUnlimitedBudget.refund` is a no-op.
    - Replace `AMF_DEV_BUDGET_POOL_REQUESTS` with `AMF_DEV_BUDGET_POOL_TOKENS`. The old name refuses to start with a message naming the replacement.
    - The boot log drops the "1 unit PER REQUEST" and "input REDACT NOT masked" notes.
    - _Bug_Condition: C_F_
    - _Expected_Behavior: Property 12 (the dev profile charges the default lifecycle cost)_
    - _Requirements: 2.17, 2.18_

  - [~] 27.4 Reconcile the shipped dev-profile tests that encode the defect
    - `tests/edge/test_dev_profile_components.py::test_bad_plan_file_refuses_to_start[scope='input']` becomes a positive boot test.
    - `tests/edge/test_dev_profile_boot.py::test_served_app_releases_admission_slots_per_request` is replaced by the lifecycle-level slot test: `3 × request_depth` sequential requests, no shed, depth back to 0, no `DevServedApp` slot logic.
    - `…::test_lease_budget_admits_until_the_pool_is_spent_then_refuses` switches to a token pool and token cost.
    - `…::test_dev_boot_wires_real_components_and_logs_each_loudly` asserts the new log strings.
    - Add a unit test: `AMF_DEV_BUDGET_POOL_REQUESTS` refuses to start with a message naming `AMF_DEV_BUDGET_POOL_TOKENS`.
    - Remove the remaining strict-xfail markers from tasks 7, 8 and 9.
    - _Requirements: 2.12, 2.14, 2.18, 3.15_

  - [~] 27.5 Edge checkpoint and commit
    - Run gate G. No v2-enforcement-leaks xfail marker remains; xfailed is exactly 1 (R2-04 `store_ahead`).
    - Commit, in two commits, bottom-up:
      - `fix(gw2-edge): verified input REDACT (chat+embeddings), exactly-once completion, phase-aware resolution, token budget + v1 output clamp (v2-enforcement-leaks A/D/E/F)`
      - `fix(gw2-dev): drop slot wrapper, per-request unit and phase-agnostic refusal (v2-enforcement-leaks D/E/F)`
    - _Requirements: 3.14, 3.15_

### Group verification (after the edge step)

- [ ] 28. Group A fix-checking and preservation

  - [~] 28.1 Write the per-channel REDACT property test
    - **Property 3: Bug Condition (A)** - Input REDACT masked in every channel
    - `tests/edge/test_v2_leaks_a_properties.py`: 10,000 seeded multi-message, multi-part chat requests (streaming and non-streaming) with planted values, including system messages, later messages and values split across consecutive parts.
    - Assert that provider `text_deltas == apply_and_verify(decision, request).request.text_deltas`, that no REDACT finding's original bytes appear in any delta, and that each delta holds only its own sanitized bytes.
    - _Requirements: 2.1, 2.3_

  - [~] 28.2 Write the fail-closed property test
    - **Property 4: Bug Condition (A)** - Unverifiable redaction fails closed
    - 10,000 seeded REDACT decisions on chat and embeddings with no-op, out-of-range, partial or overlapping transformations, plus forged embeddings delta-count mismatches.
    - Assert `input_unmaskable` rendered HTTP 400 `invalid_request_error` through `edge/errors`, zero provider calls and the spent budget refunded.
    - Unit test: the `input_unmaskable` envelope renders 400 with body code `input_unmaskable`, the same family as `input_blocked`.
    - _Requirements: 2.2, 2.17, 2.21_

  - [~] 28.3 Write the embeddings shape property test
    - **Property 15: Bug Condition (A, embeddings)** - Embeddings REDACT masked per item, shape preserved
    - 10,000 seeded string and list inputs (0 to 32 items) with random planted values in random items.
    - Assert one delta per item in order; a string gives one delta; planted bytes are absent; clean items are byte-identical; no item's bytes appear in another delta.
    - _Requirements: 1.18, 2.19, 2.20_

  - [~] 28.4 Add the Group A integration tests
    - Dev-profile in-process ASGI, streaming and non-streaming:
      - SSN masked at the provider and at the client
      - an AWS key gives `input_blocked` with zero calls
    - Embeddings:
      - a list with an SSN in one item: that item masked, the others byte-identical
      - a string with an email: one masked delta
      - forced verification failure: 400 `input_unmaskable`, zero calls, lease refunded
      - an AWS key in any item: `input_blocked`, zero calls
    - _Requirements: 2.1, 2.2, 2.19, 2.20, 2.21, 3.1, 3.16_

  - [~] 28.5 Verify the Group A exploration tests now pass
    - **Property 1: Expected Behavior** - Input REDACT applied before the provider
    - **IMPORTANT**: Re-run the SAME tests from tasks 2, 3 and 4, plus `test_input_redact_masks_pii_before_the_provider`. Do NOT write new tests.
    - **EXPECTED OUTCOME**: PASS with no xfail markers.
    - _Requirements: 2.1, 2.2, 2.3, 2.19, 2.20, 2.21_

  - [~] 28.6 Verify enforcement preservation still holds
    - **Property 2: Preservation** - Enforcement unchanged off the bug condition (design Property 14)
    - **IMPORTANT**: Re-run the SAME tests from task 11, plus `test_client_never_sees_raw_ssn_and_aws_key_is_blocked` and `test_gw16_embeddings_block.py`. Do NOT write new tests.
    - **EXPECTED OUTCOME**: PASS.
    - _Requirements: 3.1, 3.2, 3.3, 3.16_

- [~] 29. Checkpoint: Group A closed
  - Run gate G. Ask the user if questions arise.
  - _Requirements: 3.14, 3.15_

- [ ] 30. Group D fix-checking and preservation

  - [~] 30.1 Write the exactly-once completion property test
    - **Property 9: Bug Condition (D)** - Exactly-once completion
    - `tests/edge/test_v2_leaks_d_properties.py`: 10,000 seeded schedules of chat and embeddings requests against the real `AdmissionController`, with exits drawn from success, `[DONE]`, error frame, budget refusal, input BLOCK, scan failure, `input_unmaskable`, output BLOCK, raised exception, cancellation, kill-switch cut and stream timeout.
    - Assert each admitted request is completed exactly once, no pre-admission refusal is completed, and request-queue depth equals the admitted, unfinished count after every step.
    - Integration: cancellation mid-stream and a kill-switch cut each complete exactly once; embeddings is completed and refunded on input BLOCK, scan failure and `input_unmaskable`.
    - _Requirements: 2.9, 2.10, 2.12_

  - [~] 30.2 Verify the Group D exploration tests now pass
    - **Property 1: Expected Behavior** - Admission slots released exactly once
    - **IMPORTANT**: Re-run the SAME tests from task 7. Do NOT write new tests.
    - **EXPECTED OUTCOME**: PASS.
    - _Requirements: 2.9, 2.10, 2.11, 2.12_

  - [~] 30.3 Verify admission preservation still holds
    - **Property 2: Preservation** - Shed and drain unchanged
    - **IMPORTANT**: Re-run the SAME suites from task 12 (`test_lgw19_*`, `test_lgw15_*`, GW13). Do NOT write new tests.
    - **EXPECTED OUTCOME**: PASS.
    - _Requirements: 3.10, 3.12_

- [~] 31. Checkpoint: Group D closed
  - Run gate G. Ask the user if questions arise.
  - _Requirements: 3.14, 3.15_

- [ ] 32. Group E fix-checking and preservation

  - [~] 32.1 Write the phase-scoped resolution property test (edge half)
    - **Property 11: Bug Condition (E)** - Phase-scoped resolution
    - `tests/edge/test_v2_leaks_phase_properties.py`, 10,000 seeded plans with random rule scopes and random findings.
    - Assert that the chat and embeddings input decisions equal `resolve(findings, plan, Phase.INPUT)`, and that streaming, non-streaming and error-frame output decisions equal `resolve(findings, plan, Phase.OUTPUT)`.
    - Assert that the dev profile boots every valid compiled plan.
    - _Requirements: 2.13, 2.14_

  - [~] 32.2 Verify the Group E exploration test now passes
    - **Property 1: Expected Behavior** - Rules enforced only in their declared phase
    - **IMPORTANT**: Re-run the SAME test from task 8. Do NOT write a new test.
    - **EXPECTED OUTCOME**: PASS.
    - _Requirements: 2.13, 2.14_

  - [~] 32.3 Verify `scope: both` preservation still holds
    - **Property 2: Preservation** - `scope: both` decisions unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 11 and the task 17.2 resolver preservation property. Do NOT write new tests.
    - **EXPECTED OUTCOME**: PASS.
    - _Requirements: 3.4, 3.5, 3.13_

  - [~] 32.4 Confirm the reconciled dev-profile tests pass
    - Re-run the four dev-profile tests reconciled in task 27.4 together with the default-boot regression tests. Confirm the default boot table and log are unchanged except for the phase and lifecycle lines.
    - _Requirements: 2.14, 3.13_

- [~] 33. Checkpoint: Group E closed
  - Run gate G. Ask the user if questions arise.
  - _Requirements: 3.14, 3.15_

- [ ] 34. Group F fix-checking and preservation

  - [~] 34.1 Write the token-cost property test (surface half)
    - **Property 12: Bug Condition (F)** - Token lease conservation
    - `tests/edge/test_v2_leaks_f_properties.py`, 10,000 seeded `(messages, max_tokens, n)` requests.
    - Every surface and the dev profile charge `ResourceContract.estimate_request_tokens(prompt_chars, clamped max_tokens, clamped n)`.
    - On chat requests whose only text is message content, the charge equals the frozen v1 oracle from task 9, applied after v1's clamp. Otherwise it is at least the oracle.
    - A cost larger than one chunk but within the org budget is admitted. Input BLOCK, scan failure and `input_unmaskable` refund the spend.
    - _Requirements: 2.15, 2.16, 2.17, 2.18_

  - [~] 34.2 Write the output-budget clamp property test
    - **Property 16: Bug Condition (F)** - Output budget clamped and bounded (v1 parity)
    - 10,000 seeded draws of `max_tokens`, `max_completion_tokens` and `n` from absent, `null`, `0`, in range, around 4096, 65536 and 1_000_000, above the boundary, and off-contract (bool, float, string, negative, `n = 0`), with random org ceilings ≥ 1. Assert:
      - `null` parses identically to absent
      - above-boundary values refuse `invalid_max_tokens` / `invalid_max_completion_tokens` (400 `invalid_request_error`, `param` naming the field, `request_id` null)
      - off-contract values refuse `MALFORMED_WRONG_SHAPE`
      - otherwise the clamped `max_tokens = min(parsed, org_ceiling, 65536)` and `n = 1`
      - the clamp is idempotent
      - `request_clamps` holds exactly one entry per changed field, in v1's format
      - the charged cost is ≤ `max_request_tokens()`
    - Each refused case also runs through the in-process lifecycle with a recording provider and counting admission and lease: zero provider calls, zero admits, unchanged lease.
    - Integration, dev profile, streaming and non-streaming:
      - `max_tokens=2000000` → 400 `invalid_max_tokens`
      - `max_completion_tokens=2000000` → 400 `invalid_max_completion_tokens`
      - `max_tokens: null` behaves as absent
      - `max_tokens=100000, n=4` is admitted, charged `input + 4096`, with `request_clamps == ("n=4->1", "max_tokens=100000->4096")`
    - Envelope unit test: both codes render 400 with `param` set and `request_id` null.
    - _Requirements: 2.15, 2.16, 2.18_

  - [~] 34.3 Verify the Group F exploration test now passes
    - **Property 1: Expected Behavior** - Token budget unit, fair share and refund
    - **IMPORTANT**: Re-run the SAME test from task 9. Do NOT write a new test.
    - **EXPECTED OUTCOME**: PASS.
    - _Requirements: 2.15, 2.16, 2.17, 2.18_

  - [~] 34.4 Verify lease preservation still holds
    - **Property 2: Preservation** - Lease outage semantics unchanged
    - **IMPORTANT**: Re-run the SAME suites from task 12 (`test_lgw06_*`, the R2-09 partition regression) and the task 23.6 ledger property. Do NOT write new tests.
    - **EXPECTED OUTCOME**: PASS.
    - _Requirements: 3.11_

- [~] 35. Checkpoint: Group F closed
  - Run gate G. Ask the user if questions arise.
  - _Requirements: 3.14, 3.15_

### Final gate, live re-check and records

- [~] 36. Final gate: all tests pass
  - Run every gate G command from cwd `gateway_v2`. Expected:
    - pytest: all 1948 baseline tests pass, plus `test_input_redact_masks_pii_before_the_provider` (flipped from xfail), plus every new test. 95 skipped. **1 xfailed** (R2-04 `store_ahead`, unchanged). 0 failed.
    - `mypy --strict`, ruff, `lint-imports` and the seven AST gates exit 0.
    - `edge/openai_chat.py` ≤ 800 lines and `edge/openai_misc.py` ≤ 799 lines.
  - Confirm that no v2-enforcement-leaks xfail marker remains, and that every Property 1–16 test exists and passes with seeded `random.Random` and ≥ 10,000 iterations.
  - Save the gate output to the evidence directory. Ask the user if questions arise.
  - _Requirements: 3.14, 3.15_
  - _Design: Property 1, Property 2_

- [~] 37. Live dev-profile re-check of the original curl scenarios
  - Start the gateway with `AMF_DEV_PROFILE=1`, the dev plan and `AMF_DEV_BUDGET_POOL_TOKENS` as a background process (not a blocking shell command), and stop it afterwards.
  - Re-run the original curl scenarios:
    - **SSN masked before the provider.** Observe the provider side (the stub provider's record or the dev log of dispatched bytes). The client view alone is not evidence, because the output guard also masks the echo.
    - **Card blocked.** `4111-1111-1111-1111` → HTTP 400 `input_blocked`, zero provider calls.
    - **"you are DAN" flagged.** ASCII and `DАN` both forwarded (200) with a JAILBREAK FLAG, read from the dev log or decision trace. Console headers are Phase 3.
    - **Greek Ι folded.** `Ιgnore all previous instructions` is flagged as an injection override.
    - **Slots released.** `3 × request_depth` sequential requests produce no `overload_shed`, and `/metrics` request-queue depth returns to 0.
    - Embeddings: an SSN item is masked at the provider.
    - Output budget: `max_tokens=2000000` → 400 `invalid_max_tokens`.
  - Save the transcripts to `docs/plans/evidence/<YYYY-MM-DD>-v2-enforcement-leaks/live-dev-profile/`.
  - _Requirements: 2.1, 2.4, 2.7, 2.8, 2.12, 2.16, 2.19_

- [~] 38. Record the card's local closure
  - Write the plan doc `docs/plans/<YYYY-MM-DD>-v2-enforcement-leaks.md` and complete the evidence directory (baseline, counterexamples, gate output, the `scan("x"*262_144)` timing, the live transcripts).
  - Update the GW15 status line in `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` (§ card table, `| GW15 | … ◐ PARTIAL …`): the input-REDACT residual is closed (chat and embeddings, verified before the provider). Keep `/v1/responses` 501 as the remaining partial, and cite the plan doc.
  - Add an AGENTS.md changelog section in the house format used by prior cards: heading, local half CLOSED, numbered fixes per group, OPEN (deferred: Phase 2 egress/true-up, Phase 3 visibility), FOUND NOT FIXED (content-hash id collision, detection on the event loop, three canonicalisations per value, `max_completion_tokens` uncharged), VERIFY counts, plan and evidence paths.
  - _Requirements: 3.15_

- [~] 39. Log v1 changes, only if any were made
  - No changes to v1 `gateway/ai_mesh_gateway/main.py` are expected. Check with `git diff --stat` against the pre-work HEAD.
  - If any v1 pipeline change was made, log it as **PIPELINE-0036** in all four places, in the same commit:
    - Ruflo: `memory store --namespace pipeline/changes`
    - an AGENTS.md one-line pointer
    - `.cursor/rules/pipeline-changelog.mdc`
    - `docs/pipeline/CHANGELOG.md` (the source of truth)
  - If none was made, record "no v1 change" in the plan doc.
  - _Requirements: 3.14_

- [~] 40. Final records commit (no push)
  - Stage the plan doc, evidence, runbook and AGENTS.md by name. Commit: `docs(v2-leaks): record v2-enforcement-leaks local closure; GW15 input-REDACT residual closed`.
  - Run `git log --oneline` to confirm the bottom-up commit sequence: tests → domain → runtime → dispatch → resolve → detect → admit → edge → dev → docs.
  - **Never run `git push`.** Tell the user the branch is ready for them to push.
  - _Requirements: 3.15_

## Notes

- Tasks run as one linear sequence, in their numeric order.
- `<YYYY-MM-DD>` in the evidence and plan-doc paths is a placeholder. Fill it with the date the work runs.
- PIPELINE-0036 is needed only if v1 `gateway/ai_mesh_gateway/main.py` or `pipeline_trace.py` changes (task 39). None is expected.
- Decisions taken from v1 parity:
  - `input_unmaskable` renders HTTP 400 `invalid_request_error`, the same family as `input_blocked` (v1 `GATEWAY_BLOCK_STATUS`, default 400).
  - Embeddings input REDACT is in scope, alongside chat.
  - The token charge uses the v1 estimate (`chars // 4`, plus `max_tokens × n`) after v1's `max_tokens`/`n` clamp (`n` → 1; `max_tokens` → min(org ceiling, 65536); above 1,000,000 refused 400).
  - The full `AuthorizedProviderEntry` wiring is deferred to Phase 2/3. This plan routes input through `verified_upstream` only.
