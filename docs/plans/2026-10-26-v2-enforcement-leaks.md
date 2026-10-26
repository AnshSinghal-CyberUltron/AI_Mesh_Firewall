# v2-enforcement-leaks / GW15 — six enforcement leaks closed before the provider

**Status:** implemented, local half CLOSED. **Card:** GW15 (B1 chat surface, Phase 1).
**Severity:** CRITICAL (each leak lets a tenant-selected policy be silently defeated, or leaks a
bounded resource). **Date:** 2026-10-26. **Spec:** `.kiro/specs/v2-enforcement-leaks/`.
**Evidence:** `docs/plans/evidence/2026-10-22-v2-enforcement-leaks/`.

---

## 1. Executive summary

Running `gateway_v2` through the opt-in dev profile (`AMF_DEV_PROFILE=1`, plan
`gateway_v2/dev/plan.example.json`: pii → redact, secret/credential → block,
prompt_injection/jailbreak/command → flag, every rule `scope: both`) exposed six enforcement
defects in the clean-room gateway. Each was grounded in a runbook clause, not a guess, and the
runbook `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` is the sole source of truth.

| Group | Leak | Impact class |
|---|---|---|
| **A** | Input REDACT is not applied before the provider (chat **and** `/v1/embeddings`) — PII reaches the provider raw while the trace says masked | CRITICAL (C21, §0-A.1) |
| **B** | Luhn-valid card numbers (PCI PAN) are never detected — the `pii_phone` prefilter consumes the digit run leftmost-first and the card branch is never evaluated; no Luhn check | CRITICAL (GW09) |
| **C** | `you are DAN` is missed (ASCII and homoglyph spellings) because the `\bDAN\b` prefilter anchors the confirm past the phrase start; Greek confusables (e.g. `Ι` U+0399) are unfolded | CRITICAL (GW09, L07-2, GW10 C6) |
| **D** | Admission slots leak — the lifecycle admits but never calls `complete`, and `complete` pops a foreign FIFO slot; the in-flight table grows without bound | HIGH (GW19, §10.4) |
| **E** | The resolver is not phase-aware — one resolver bound to one phase serves both input and output, so `scope: input`/`output` rules cannot be honoured and the dev profile refuses to boot them | HIGH (§10.5.2/3, GW07) |
| **F** | The budget cost unit does not match the lease unit — GW06 sizes the chunk in requests, GW15 charges characters, so a long prompt can never be admitted; a refused request keeps its charge; the output budget is uncharged and unbounded | HIGH (GW06, C29, R2-09) |

The fixes land **bottom-up by layer** (the design's "Order of changes": domain → runtime →
dispatch → resolve → detect → admit → edge, then the dev-profile cleanup), one commit per layer,
fail-closed everywhere, no capacity literal outside `runtime/resources.py`, `egress` never
importing `detect`. Phase 0 wrote one exploratory bug-condition test per group plus the
preservation baselines on the unfixed tree before any product change; each exploratory case that
failed was committed under `@pytest.mark.xfail(strict=True)` so the suite stayed green between
layer commits and the marker had to be removed the moment its fix landed.

**No v1 change.** This card touched no v1 code: `git diff --stat 103502b6..HEAD --
gateway/ai_mesh_gateway/` is empty and there is no uncommitted v1 change, so the v1
`pipeline/CHANGELOG.md` + PIPELINE-xxxx pointer is **not** needed. All work is in the clean-room
`gateway_v2/` tree.

---

## 2. The six leaks and their fixes

### Group A — input REDACT applied and verified before the provider (chat + embeddings)

**Root cause.** On a REDACT input decision the gateway forwarded the raw bytes: only the output
guard masked, and only on the echoed reply (C21 — the trace said masked while the provider saw
raw). `EmbeddingsSurface._input_scan` acted on a decision only when it was BLOCK and ignored
REDACT, so a redactable embedding item reached the provider raw. The dispatch `_resplit` also
moved one byte of the next delta into the redacted delta when a replacement changed length, and
the detector-keyed verify obligation raised a false `VerificationFailure` on a mixed
`SECRET→flag` + `CREDENTIAL→redact` plan.

**Fix (layers domain, dispatch, resolve, edge).**
1. `domain/decision.py`: `FindingDisposition.finding_index` keys each obligation to the finding it
   belongs to (default `None` keeps every existing construction compiling).
2. `resolve/resolver.py`: set `finding_index` on every per-finding disposition; coalesce
   overlapping/identical REDACT spans into their union, never merging spans that merely touch
   (`a.end == b.start`), so adjacent channels stay independent.
3. `dispatch/verify.py`: oblige exactly the indexed findings' spans when every per-finding
   disposition carries a `finding_index`, else fall back to the shipped detector-keyed rule; fix
   `_resplit` so each delta boundary shifts by the summed length-delta of the transformations
   ending at or before it, and a straddling transformation stays in the delta where it starts —
   raw bytes never cross a channel. Extract `verified_upstream(auth, *, decision, request,
   transform)` so there is ONE path to the provider; `VerificationFailure` propagates.
4. `edge/input_enforcement.py` + `edge/embeddings_input.py`: `scan_deltas` runs the detector once
   per delta plus once per joined content-part run, shifts spans by each channel's base offset,
   de-dupes then resolves once; `stage_upstream`/`stage_embeddings` authorize → `verified_upstream`
   → fail closed with `input_unmaskable` (HTTP 400 `invalid_request_error`, same family as
   `input_blocked`, zero provider calls) when the transformed bytes cannot be verified or an
   embeddings shape check fails.

### Group B — Luhn-valid cards detected regardless of pattern order

**Root cause.** The multi-pattern pass is leftmost-first: the `pii_phone` prefilter consumed the
PAN's digit run, its precise matcher rejected it, and the `credential_card` branch was never
evaluated over that region. The card pattern also had no checksum.

**Fix (layer detect).**
1. `detect/deterministic/patterns.py`: `PatternSpec.validator`; `credential_card.validator =
   "luhn"`. Bound the previously unbounded prefilters (email local `{1,64}`, domain `{1,253}`,
   phone `{9,14}`, AWS key `{10,16}`, API-key value `{12,512}`) so no prefilter can swallow an
   arbitrarily long region.
2. `detect/deterministic/matcher.py`: keep the one `_COMBINED.finditer` primary pass; apply the
   pattern's validator after a confirm; on a failed confirm/validator over region `R`, rescan
   every other pattern leftmost-first inside `R` (single-exclusion combined regexes precompiled at
   import; deeper sets via the once-compiled `_ALL_AT` per-position lookahead restricted to the
   nested region), so every confirmable match of every pattern emits regardless of order — within
   GW09's single canonicalization pass and single primary match.

`4111-1111-1111-1111`, `4111111111111111` and `4111 1111 1111 1111` now each emit one
`credential_card` CREDENTIAL finding over the whole PAN; `4111-1111-1111-1112` (Luhn-invalid) and
12-/20-digit runs emit none.

### Group C — jailbreak phrases and confusables covered

**Root cause.** The `\bDAN\b` prefilter anchored the precise `you are DAN` confirm at the `DAN`
offset, where the phrase can never match (the same matcher defect as B). Greek capital Iota
(U+0399) was absent from the fold table, so `Ιgnore all previous instructions` was never folded to
Latin and produced no hit.

**Fix (layer detect).**
1. New `detect/confusables.py`: a versioned `CONFUSABLES` mapping (`CONFUSABLE_SET_VERSION =
   "amf-confusables-1"`, a sha256 `CONFUSABLES_DIGEST`), a superset of both prior tables adding the
   Greek and Cyrillic upper- and lower-case letters whose UTS #39 prototype is one ASCII letter.
   `canonicalize._fold_homoglyphs` and `semantic/language.CONFUSABLE_FOLD` both read this one table
   (1:1 per character, so span records are unchanged); `ruleset_version()` appends the confusables
   record and the validator field, so a change to the set changes `detector_version`.
2. Lead-context jailbreak alternatives added before the existing ones (`you\s+are\s+DAN\b`,
   `enable\s+developer\s+mode`, `answer\s+without\s+(?:any\s+)?restrictions`), each audited for the
   start-superset invariant; combined with the Group B failed-region rescan the phrase now matches
   from its start and its span maps back to the original (confusable-bearing) bytes.

### Group D — admission slots released exactly once, `complete` isolated

**Root cause.** The GW15 lifecycle admitted through `AdmissionController.admit` but never called
`complete`, so request-queue slots were never released and in-flight records never pruned.
`complete` popped the FIFO head for any id — including an unknown or already-completed id —
releasing another in-flight request's slot.

**Fix (layers admit, edge).**
1. `admit/queues.py`: `BoundedQueue.remove(item)` removes one occurrence plus its aligned
   `_enqueued_at` (O(depth), depth contract-bounded).
2. `admit/admission.py`: `complete(request_id)` returns untouched for an unknown id; otherwise it
   removes exactly that id's slot, decrements the admit refcount, prunes the in-flight record at 0
   and publishes the queue. `_decide` increments the refcount on admit (content-hashed ids can
   collide).
3. `edge/request_holds.py`: a per-request `RequestHolds` (`__slots__`, never shared) records
   `admitted`/`spent`/`mark_dispatched`; `release(admission, quota)` runs once behind a latch,
   refunds if spent-and-not-dispatched then completes if admitted — synchronous, never awaits,
   never raises. Wired in `edge/routes.py` with `try: run_lifecycle(...) finally:
   holds.release(...)`, `mark_dispatched()` immediately before each `provider.open`. The dev
   profile's ASGI slot wrapper (`DevCompletingAdmission`, `DevServedApp.begin/end`) is removed, so
   both boot paths release through the one lifecycle.

Under the real `AdmissionController`, request-queue depth returns to 0 after `3 × request_depth`
sequential requests with no `overload_shed`.

### Group E — phase-aware resolution

**Root cause.** One resolver bound to one `Phase` served both input and output decisions, so a
`scope: input` rule applied to output (and vice versa) or was dropped in its target phase, and the
dev profile refused to boot any non-`both` plan.

**Fix (layers resolve, edge).**
1. `resolve/resolver.py`: `resolve(findings, plan, phase)` selects only rules scoped `phase` or
   `both` (Property 11).
2. `edge/phase_resolvers.py`: `resolver_for(route, plan, phase)` returns the route's
   `phase_resolvers(plan, phase)` when set, else `route.resolver` (the unset branch reproduces
   today's default boot). Chat `_input_scan` resolves at `Phase.INPUT`; the output guard,
   non-stream output and mid-stream error-frame scan resolve at `Phase.OUTPUT` with the request's
   pinned plan. The dev profile drops the `require_phase_agnostic` scope check and wires a
   `phase_resolver_factory` returning `PlanResolver(plan=plan, phase=phase)`.

### Group F — one token unit for cost and lease, with refund and a bounded output budget

**Root cause.** GW06 sized the lease chunk in requests while GW15 charged characters, so any prompt
whose character cost exceeded the chunk was refused `budget_unavailable` while the org budget still
had room. A refused request (input BLOCK, scan failure) kept its spent budget. The output budget
(`max_tokens`, `n`) was neither charged nor bounded.

**Fix (layers runtime, admit, edge).**
1. `runtime/resources.py`: `estimate_request_tokens(chars, max_tokens, n)` — v1's estimate
   (`input = max(1, chars // 4)` when chars > 0 else 0, plus `max_tokens * n`, min total 1);
   `_CHARS_PER_TOKEN_DIVISOR = 4`; the four v1 output-budget constants with an import-time ordering
   check (`_OUTPUT_TOKENS_HARD_CAP = 65536`, `_DEFAULT_RESPONSE_TOKEN_CEILING = 4096`,
   `_MAX_OUTPUT_TOKENS_BOUNDARY = 1_000_000`, `_MAX_COMPLETIONS_PER_REQUEST = 1`);
   `max_request_tokens()`; `store_keys.budget_workers(org)` (a `{rv2}` ZSET scored by lease expiry).
2. `admit/lease.py`: `acquire` computes `active` from a store-clock-pruned `budget_workers` ZSET and
   grants `min(pool, cap, max(1, pool // active))`; `BudgetLease(overdraft_tokens=...)` lets a cost
   up to the overdraft go negative and schedule a refill (overshoot published on
   `amf_quota_lease_overshoot`); `refund(cost, generation)` restores locally within the same
   generation. `admit/quota.py`: `derive_lease_cap_tokens` and `QuotaComponent.refund`.
3. `edge/wire/openai.py`: parse `max_tokens`/`n`/`max_completion_tokens` through one `_int_field`
   (off-contract → `MALFORMED_WRONG_SHAPE`; above the boundary → `invalid_max_tokens` /
   `invalid_max_completion_tokens` with `param`); pure `clamp_output_budget` (n then max_tokens,
   idempotent) and `prompt_chars`. `edge/openai_chat.py` charges
   `estimate_request_tokens(prompt_chars(req), req.max_tokens, req.n)` and clamps against the org
   ceiling; `edge/errors.py` maps `input_unmaskable`, `invalid_max_tokens` and
   `invalid_max_completion_tokens` to `(400, "invalid_request_error")`, the boundary rendered from
   `max_output_tokens_boundary()` not a literal.

A 2,000-char prompt with a request-count chunk is now admitted; `max_tokens=2000000` is refused
HTTP 400 `invalid_max_tokens` (param `max_tokens`) before admission; an input BLOCK after a spend
refunds on both chat and embeddings; a 4-worker grant is at most `pool // 4`.

---

## 3. The bottom-up commit sequence

One commit per layer, staged by name, newest first (the docs/records commit is deferred):

```
f56160c1  fix(gw2-dev):   drop slot wrapper, per-request unit and phase-agnostic refusal (D/E/F)
d36caac6  fix(gw2-edge):  verified input REDACT (chat+embeddings), exactly-once completion,
                          phase-aware resolution, token budget + v1 output clamp (A/D/E/F)
2fc03f72  fix(gw2-admit): slot-exact pruning complete + fair-share token lease, overdraft,
                          refund (D/F)
1104a90e  \
43dd5afd   > fix(gw2-detect): failed-region rescan + Luhn + bounded prefilters + versioned
143ca97d  /  confusables (B/C)
9835e557  fix(gw2-resolve):  finding_index + REDACT span coalescing (A)
47d1c182  fix(gw2-dispatch): per-delta resplit, finding-keyed obligations, verified_upstream (A)
56a3ec7a  feat(gw2-runtime): token estimate + v1 output-budget contract + budget_workers key (F)
8328a399  feat(gw2-domain):  FindingDisposition.finding_index (A)
103502b6  test(v2-leaks):    exploratory bug-condition + preservation baselines (unfixed tree)
```

The docs/records commit (this plan doc, the runbook GW15 line, the AGENTS.md changelog, the
evidence directory) is the final commit and was NOT made as part of this authoring step.

---

## 4. Verification

### 4.1 Final gate (all green)

Run from cwd `gateway_v2` (`.venv/bin`), full evidence in
`docs/plans/evidence/2026-10-22-v2-enforcement-leaks/final-gate.txt`.

| Gate | Result |
|---|---|
| `pytest -q -p no:cacheprovider` (offline) | **2415 passed, 95 skipped, 1 xfailed, 0 failed** |
| `mypy --strict` | clean, 219 source files |
| `ruff check gateway_v2 state_control lint tests` | clean |
| `lint-imports` | 3 contracts kept, 0 broken |
| 7 AST gates (`check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_sizes`, `check_tenant_scale`, `check_dispatch_provenance`) | each exit 0 |
| Edge file sizes | `edge/openai_chat.py` 800 (≤ 800), `edge/openai_misc.py` 798 (≤ 799) |

Relative to the unfixed baseline (1948 passed / 95 skipped / 2 xfailed, see `baseline.txt`): every
baseline test still passes, `test_input_redact_masks_pii_before_the_provider` flipped from xfail to
PASS, every new test passes, skipped unchanged at 95, xfailed dropped 2 → 1. The one surviving xfail
is ONLY `tests/state_control/test_r2_04_service.py::test_a_store_ahead_repair_settles_instead_of_looping`
— the pre-existing R2-04 `store_ahead` strict xfail, unrelated and unchanged. No v2-enforcement-leaks
xfail marker remains active.

Every design Property 1–16 has a fix-checking/exploration test in the house idiom (seeded
`random.Random`, ≥ 10,000 iterations, the seed in each assertion message, no hypothesis); the full
inventory is in `final-gate.txt`.

### 4.2 Group-B structural cost

`scan("x" * 262_144)` (the C26 decode-budget / 256 KiB edge) dropped from **~63.1 s on the unfixed
tree to ~0.11 s** on the fixed tree (`scan-262144-timing.txt`; a local single-shot measurement, the
structural `≤ c·len(text)` bound is the P6 assertion). The bounded prefilters plus the once-compiled
single-exclusion / `_ALL_AT` rescan remove the leftmost-first whole-region cascade.

### 4.3 Live dev-profile re-check (task 37)

All 7 original curl scenarios PASS over the REAL uvicorn HTTP server (uvicorn 0.54.0; real GW06
lease over a local Redis container; 9 workers each acquiring a real token lease chunk), with a
complementary in-process recording-provider run for byte-level proof. Full transcript + raw curl +
server log in `docs/plans/evidence/2026-10-22-v2-enforcement-leaks/live-dev-profile/`.

| # | Scenario | Observed | Verdict |
|---|---|---|---|
| 1 | SSN masked before the provider (stream + non-stream) | provider received `My SSN is [REDACTED] thanks`, raw SSN absent | PASS |
| 2 | Card `4111-1111-1111-1111` | HTTP 400 `input_blocked`, zero provider calls | PASS |
| 3 | `you are DAN` ASCII + Cyrillic `DАN` | both HTTP 200, provider `jailbreak=1` each | PASS |
| 4 | Greek `Ιgnore all previous instructions` | HTTP 200, provider `prompt_injection=1` | PASS |
| 5 | Slots released (`3 × request_depth`) | 42/42 HTTP 200, 0 `overload_shed`; in-proc depth 0 after each, 0 in-flight | PASS |
| 6 | Embeddings SSN item | item masked at the provider (`release notes` kept byte-identical) | PASS |
| 7 | `max_tokens=2000000` | HTTP 400 `invalid_max_tokens`, `param` `max_tokens`, before admission | PASS |

---

## 5. Open, and honestly partial

| Item | Why it is not closed here |
|---|---|
| **Phase 2 — provider egress + true-up** | The real `HttpProviderClient` seams and C29's "true-up against actual usage" need provider-reported usage; v1's post-forward reconcile stays out of scope. This card pre-charges the v1 estimate only. |
| **Phase 3 — decision visibility** | Audit, the `X-ZeroShield-*` headers and the console contract (GW14b) are Phase 3. |
| **`/v1/responses`** | Still a 501 stub (`edge/openai_responses.py`). GW15 stays **◐ PARTIAL** on this one surface; the input-REDACT residual is the only part this card closes. MCP, RAG and vector surfaces are out of scope (GW17/GW18). |
| **Full `AuthorizedProviderEntry` on chat** | Provider pooling, audit and rewrite belong to Phase 2/3; v1 parity is the target but v1 does not decide the phasing. |

---

## 6. Found, not fixed

Recorded rather than fixed, each out of this card's scope:

1. **Content-hash request-id collision.** Admitted request ids can collide (content-hashed), so
   `_decide` carries an admit refcount and `complete` decrements it. The collision itself is a
   GW05/identity concern; the lifecycle is made collision-safe but the id scheme is unchanged.
2. **Detection runs on the event loop.** Canonicalization + the deterministic match run inline on
   the request path; there is no offload. Acceptable at the current single-value sizes (§4.2) but
   not moved off the loop — a GW12/executor concern.
3. **Three canonicalizations per value.** A value can be canonicalized up to three times across the
   input scan, the join-run scan and the verify step. Correct but redundant; a single shared
   canonical form is a later detect-path optimisation.
4. **`max_completion_tokens` validated but uncharged.** The field is parsed and boundary-checked
   (`invalid_max_completion_tokens`), but the budget charges `max_tokens × n` only, so a request
   that uses `max_completion_tokens` instead of `max_tokens` is under-charged on the output side.
   The charge-side fix waits on the Phase 2 usage true-up.

---

## 7. Files

**New.** `gateway_v2/detect/confusables.py`, `gateway_v2/edge/request_holds.py`,
`gateway_v2/edge/phase_resolvers.py`, `gateway_v2/edge/input_enforcement.py`,
`gateway_v2/edge/embeddings_input.py`, plus the Phase-0 exploration/preservation tests and the
per-property fix-checking tests under `gateway_v2/tests/{edge,dispatch,detect,resolve,admit,runtime}/`
(`test_v2_leaks_*`).

**Modified.** `gateway_v2/domain/decision.py` (`finding_index`),
`gateway_v2/runtime/resources.py` (token estimate + output-budget contract),
`gateway_v2/runtime/store_keys.py` (`budget_workers`), `gateway_v2/dispatch/{verify,authorized}.py`,
`gateway_v2/resolve/resolver.py`, `gateway_v2/detect/deterministic/{patterns,matcher,canonicalize}.py`,
`gateway_v2/detect/semantic/language.py`, `gateway_v2/admit/{queues,admission,lease,quota}.py`,
`gateway_v2/edge/{wire/openai,wire/openai_misc,chat_deps,openai_chat,openai_misc,openai_nonstream,routes,error_frame_scan,errors,dev_admit,dev_components,dev_profile}.py`,
and the shipped suites reconciled where they encoded a defect (GW09 detect, LGW19 admission,
dev-profile boot).

**Evidence.** `docs/plans/evidence/2026-10-22-v2-enforcement-leaks/`: `baseline.txt`,
`preservation-baseline.txt`, `counterexamples.md`, `scan-262144-timing.txt`, `final-gate.txt`,
`live-dev-profile/` (`transcript.md`, `live-curl-raw.txt`, `inprocess.txt`, `server.log`,
`_launch_server.sh`, `_inprocess_driver.py`).
