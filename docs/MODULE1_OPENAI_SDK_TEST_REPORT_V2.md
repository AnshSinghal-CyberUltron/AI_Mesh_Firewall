# ZeroShield AI Mesh Firewall — Module 1
## OpenAI-SDK Test Report **v2** — Blind-Spot Campaign

| | |
|---|---|
| **Date** | 2026-07-20 |
| **Predecessor** | `docs/MODULE1_OPENAI_SDK_TEST_REPORT.md` (v1, 25 findings, all fixed in `b67fd742`) |
| **HEAD at completion** | `a0834d3b` |
| **New tests** | **259** across 8 files, 6 parallel suites |
| **Full suite** | 70 failed · **2848 passed** · 18 skipped · 24 xfailed (baseline was 70 failed / 2603 passed) |
| **Regressions** | **zero** — failing set byte-identical to the pre-campaign baseline |
| **New defects found** | **5 regressions/gaps in code shipped by v1** (1 CRITICAL, 2 self-inflicted regressions) **+ 3 pre-existing tier-2 defects** (1 CRITICAL, 2 HIGH) |

---

## 1. Why this is not a re-run of v1

v1 already tested Module 1 through the stock SDK and produced 25 findings, all since
fixed and pushed. Repeating it would mostly re-confirm known results.

The valuable target was v1's **own admitted limitations**. Every one was verified still
open at the start of this campaign:

| v1 blind spot | Verified at campaign start |
|---|---|
| Shared harness sets `AGENT_ID = None` | still present — short-circuits ~1,900 lines |
| `tier2_enabled: False` everywhere | still `False`; production ships `ENABLE_TIER2=true` |
| `/v1/rag` requests in the M1 suites | **zero** |
| "Cross-tenant" tests | header-spoofing with a **single** key |
| Streaming coverage for §§1.1–1.5 | none |
| ENFORCED claims adversarially checked | 0 of ~41 |

v1 stated the consequence plainly: there are **two** non-streaming output-guard
implementations, and its suites exercised only the standalone one — so the CRITICAL
I-01 fix was validated against the wrong implementation. That was this campaign's
central question.

---

## 2. Results by suite

| Suite | File | Result | What it settles |
|---|---|---|---|
| Connected path | `test_v2_connected_path_sdk.py` | 29 passed, 1 xfailed | I-01 fix holds on the **connected** guard |
| Cross-tenant | `test_v2_cross_tenant_sdk.py` | 20 passed | A→B isolation, previously unproven |
| Tier-2 | `test_v2_tier2_detection_sdk.py` | 35 passed, 3 xfailed | **1 regression fixed, 2 HIGH + 1 CRITICAL found** |
| Vector / RAG | `test_v2_vector_rag_firewall.py` | 58 passed | §1.3, previously at zero coverage |
| Streaming parity | `test_v2_streaming_parity_sdk.py` | 66 passed | §§1.1–1.5 flip-a-flag bypass ruled out |
| Adversarial | `test_v2_fix_adversarial.py` | 40 passed, 4 xfailed | attacks on the 25 v1 fixes |
| Regression locks | `test_fix_i22_*`, `test_fix_a03_*` | 3 passed | the two fixes made during this campaign |

---

## 3. Findings

### 3.1 A-01 — raw PII/credentials ship in `tool_calls[].custom.input`
**CRITICAL · CONFIRMED · FIXED**

The OpenAI SDK ships **two** tool-call shapes:

```
ChatCompletionMessageFunctionToolCall → {"type":"function","function":{name, arguments}}
ChatCompletionMessageCustomToolCall   → {"type":"custom","custom":{name, input}}
```

All three fold/clear sites read `tc["function"]` only. A secret in `custom.input` was
therefore never scanned, never counted as delivered, and never neutralized — the response
shipped `200 OK` with `action='allow'`. **This is the exact silent-leak shape I-01 was
filed for, in a channel the I-01 fix did not cover.**

Evidence was established without relying on the stub's semantics:
- a **benign** canary in `custom.input` round-trips to the client at 200 — proving the
  channel is genuinely client-delivered (vacuity guard);
- the **same SSN** in `function.arguments`, same rig, same guard — caught (control);
- request side has no tool-type allowlist, only a count cap, so both directions are
  passthrough.

**Fix.** Not three parallel edits — a shared `_tool_call_text_channels(tc)` iterator now
feeds the scan-text extractor, the delivered-text extractor and the neutralizer. Three
copies of a channel list is precisely what let `custom` be missed in all three places at
once; a new tool shape now lands in one place.

### 3.2 A-02 / I-22 — the v1 fix introduced a detection-starvation bypass
**HIGH · CONFIRMED REGRESSION · FIXED**

v1 raised the nested-scan depth cap 6 → 24 and added a 5,000-node budget to bound breadth
DoS. But the walk visits keys in **attacker-controlled order**, so padding `agent_data`
with benign nodes exhausts the budget before the payload is reached. Measured directly:

```
PRE-FIX  (depth 6, no node budget):     injection scanned = True   ← caught
POST-FIX (depth 24 + 5000-node budget): injection scanned = False  ← MISSED
control  (small breadth):               injection scanned = True   ← mechanism works
```

Cost to mount: **44,434 bytes** — inside any normal request limit. The bound added to stop
a DoS had become a cheaper detection bypass, and it truncated **silently**.

**Fix, two layers** (either alone is insufficient):
1. Budget → 200,000 nodes. Independently re-measured attack cost: **2,094,423 bytes**
   (a 47× increase), which collides with body/DoS limits first.
2. **Truncation fails closed.** Verified end-to-end: starvation payload → `REFUSED 400`,
   upstream reached: `0`.

> **Note on the invariant.** The original test asserted the *collector* always reaches the
> payload. That is unachievable for **any** finite budget under attacker-controlled
> ordering, so it could never pass. The correct invariant is end-to-end: a scan that could
> not complete must never be read as clean. Locked in
> `test_fix_i22_starvation_failclosed.py`.

### 3.3 A-07 — the fail-closed reached 1 of 3 call sites
**LOW-MEDIUM · CONFIRMED · FIXED**

Immediately after the I-22 fail-closed landed, the adversary checked whether it had been
applied *consistently*. It had not: two RAG metadata sites still read a possibly-partial
walk as complete.

- **RAG egress** (doc-metadata injection backstop) — a starved walk lets the backstop pass
  the metadata-borne injection it exists to catch.
- **RAG ingest** — worse: poisoned metadata stored once round-trips to **every later
  query**, so one starved walk becomes a persistent leak.

Both are on the one input surface that is attacker-controlled by design. Both now fail
closed (ingest blocks the document; egress marks it unscannable so the injection gate
drops it).

### 3.4 A-03 — the tenancy detector measured the wrong object
**MEDIUM · CONFIRMED · FIXED**

`_catalogue_spans_multiple_orgs()` counted `CONFIG_SYNC._model_routing_by_org`, but the
thing it protects is the merged router catalogue. Those populate on **different branches
of the same loop** (`config_sync.py:578-595`): a tenant whose payload carries a malformed
`routing` section skips per-org registration, while `all_models.extend(...)` runs
unconditionally. Detector counts one tenant, router holds two, org-less key enumerates
both.

**Fix.** Derive tenancy from the H7 `_zs_org` tag stamped on every deployment that reaches
the router — the object actually being protected, so it cannot drift.

> **Correction to the proposed fix.** The suggested one-liner
> `{m.get("_zs_org") for m in LLM_ROUTER.get_model_list()}` does **not** work:
> `get_model_list()` builds a fresh OpenAI-shaped projection (`{id, object, owned_by,
> model_id}`) and the tag does not survive it. The fix reads the router's **raw** entries.
> This also means any rig that stubs `get_model_list` cannot exercise the signal at all —
> with only that projection, "one org with two models" and "two orgs with one each" are
> genuinely indistinguishable.

### 3.5 Tier-2 findings — invisible to every tier-1-only campaign
**1 regression (FIXED) · 2 HIGH · 1 CRITICAL — all OPEN except the first**

v1 ran with `tier2_enabled: False` while production ships `ENABLE_TIER2=true`. Turning the
second tier on surfaced four defects that no previous run could have seen.

**3.5.1 — my own I-19 fix broke the SDK error contract under Tier-2. FIXED.**
The v1 I-19 fix passed `error_code_override=scan_verdict.reason_code` unconditionally, with
a comment asserting *"only the SIZE branch sets reason_code."* That is **false** once
Tier-2 is enabled: every tier-2 block sets one (`model_recommendation`,
`model_recommended_block`, `score_threshold_block` — `scanner.py:2244-2333`). Those internal
labels **replaced** `error.code="content_filter"`, so a stock-SDK client doing
`except APIStatusError as e: if e.code == "content_filter"` silently stopped recognising
blocks the moment `ENABLE_TIER2=true` — i.e. in the default production posture.
**Fix:** the override is now **allowlisted** to codes that are genuinely part of the OpenAI
error vocabulary (`_OPENAI_STANDARD_ERROR_CODES`); internal detector labels can no longer
reach the client's `e.code`.

**3.5.2 — `tier2_input_fail_closed` is a no-op end to end. HIGH · OPEN.**
`scanner.py:2276-2285` correctly returns `action='block'` on a degraded scan, but
`main.py:7686-7690` recomputes the enforcement input as
`scan_meta['recommended_action'] or verdict.action`, and on the degraded path that field
holds the guard model's own `'monitor'` (`bedrock_scanner.py:418` / `:471`).
`enforcement.py:284` therefore resolves `monitor`, not `block`, and the request is
forwarded to the LLM. **The operator's only fail-closed lever for the input path does
nothing on precisely the path it exists for.**

**3.5.3 — the score-threshold block is unreachable through the API. HIGH · OPEN.**
Same root cause on a non-degraded path. When the guard model returns
`recommended_action='allow'` but a risk score above `BEDROCK_BLOCK_THRESHOLD` (0.70),
`scanner.py:2320-2330` escalates to `action='block'` — and `main.py:7686` then prefers
`scan_meta['recommended_action']` (`'allow'`), discarding the escalation.

**3.5.4 — blocked output text is echoed to the caller. CRITICAL · OPEN (pre-existing).**
`main.py:800` scrubs per-stage *evidence* via `_scrub_trace_for_client`, but the generator
stage's `prompt_out` / `content` fields carry the **full blocked completion**, so a 403/400
body hands the client exactly the bytes the block existed to withhold. Pre-existing and
independently known, but this campaign proves it reachable via the tier-2 path.

> 3.5.2–3.5.4 are **not** regressions from this work and are left OPEN deliberately:
> 3.5.2/3.5.3 share one root cause (`main.py:7686` preferring the guard model's advisory
> field over the scanner's resolved verdict) and 3.5.4 touches the client trace contract.
> Both warrant their own change with their own verification, not a tail-end patch.

### 3.6 CP-01 — topology scrub absent on the standalone return
**MEDIUM · CONFIRMED · OPEN (consequence of a deliberate v1 design choice)**

The provider-topology scrub lives inside the connected branch with no counterpart on the
standalone return, so a genuinely-standalone worker still ships `provider`, `citations`,
`usage.cost` and `usage.is_byok`. v1 deliberately preserved the standalone fast path when
fixing I-02, to avoid breaking single-binary deployments. This quantifies what that choice
costs. Not fixed here — it is a product decision about standalone deployments.

### 3.7 Documented residuals — deliberately **not** fixed

| ID | Severity | Why not fixed |
|---|---|---|
| **A-04** | MEDIUM | HTTP-fallback `rewrite` carries no hints, so the strip reverts to pre-fix behaviour while telemetry says `action='rewrite'`. Requires a **control-plane** change. Exposure is narrow: only deployments that explicitly set `policy_cache_require_loaded=False` **and** are in a policy-sync outage/warm-up; the default posture returns 503+block. |
| **A-05** | LOW | A `{"type":"refusal","refusal":…}` content part folds to `""`. **Reachability unproven** — no provider was shown to emit this shape on `/v1/chat/completions`. Recorded as hardening, not actioned as a live leak. |
| **A-06** | LOW | A secret split across `top_logprobs` **alternates**. Deliberately not implemented: alternates are *substitutions*, not sequential text; concatenating them would manufacture matches from unrelated candidate tokens and produce false positives at scale. Provider-dependent, not attacker-drivable. |
| **A-03b** | INFO | A tenant slugged `default` collides with the reserved global bucket for unrelated reasons. Near-refuted; not actioned. |

---

## 4. What now has coverage that had none

**Cross-tenant (§1.1 / §1.3).** v1 stated: *"There is NO genuine cross-tenant test anywhere
in the campaign."* Now 20 tests with two real keys under two non-empty `org_slug`s:
catalogue scoping, `models.retrieve`, `owned_by` leakage, dispatch org-scoping, **BYOK
credential selection against the real router**, embeddings, kill-switch and model-state
keying, org+key rate buckets, Responses-store IDOR (`retrieve` / `input_items` / `delete` /
`previous_response_id` replay), per-tenant enforcement mode, `config_sync` never queried
with a peer slug, telemetry attribution, trace leakage, spoofed headers against a **real**
peer, and org-less keys never served a tenant's BYOK credential. **Isolation holds.**

**Vector / RAG (§1.3).** v1: *"Grep for `/v1/rag`: ZERO — nobody ever issued a request to a
RAG endpoint."* Now 72 real requests (`/v1/rag/query` ×40, `/v1/rag/ingest` ×16,
`/v1/vector/query` ×11, `/v1/vector/upsert` ×5) across 58 tests with 32 control/vacuity
guards.

> v1 also concluded `/v1/vector/*` was "unmounted in production." **That was wrong.** The
> routes mount inside the startup hook; they 404 only because `httpx.ASGITransport` does
> not run lifespan.

**Streaming parity (§§1.1–1.5).** 66 tests. All controls **PARITY-HELD**; **no GAP found** —
zero flip-a-flag bypasses. Two differences are by design and proven content-safe: a blocked
stream is a JSON 400 with **zero `data:` bytes** (proven over a real socket), and the TPM
gate is a pre-stream 429.

The strongest single result is **P-12-I**, the one control whose implementations genuinely
differ — a complete-body scan non-streaming versus `secure_streaming`'s incremental buffer.
Identical upstream text produced byte-identical client output on both transports, with the
mask genuinely firing (non-vacuous):

```
NS: 'Sure — the customer SSN on file is ***-**-6789, sent to b***@e***.com.'
ST: 'Sure — the customer SSN on file is ***-**-6789, sent to b***@e***.com.'
actions: redact | redact
```

Anti-vacuity is enforced structurally: the comparator is proven able to fail, the stream
leg is proven to actually stream (`chunk_count > 1`), the fixture asymmetry behind v1's
*refuted* "block downgrades to redact" finding is re-injected and asserted to trip the
guard, and each leg is fingerprinted (org config, process CONFIG, policy verdict,
catalogue, `AGENT_ID`, limiter, key payload) so a comparison is refused if anything moved
between them. §1.5 cells assert the **specific** routed model rather than mere equality,
so they cannot pass by both legs trivially echoing the request.

**Connected path.** The fixture is **self-validating**: it asserts connected-only
governance ran *and* runs a `connected=False` control proving the standalone path does not
reach it. Without that discriminator the suite could have passed while testing the wrong
branch — the exact failure that undermined v1.

---

## 5. Corrections to the record

Stated plainly, because each was asserted with more confidence than the evidence supported:

1. **The streaming "hang" explanation was wrong.** It was reported as *"a transient
   mid-edit race — not a product defect and not a persistent fixture bug."* It **was** a
   real fixture bug: `backend_url="http://control-plane.invalid"` on the connected path
   reaches the real `_policy_check` → blocking `urllib.request.urlopen` with a 30s
   per-attempt timeout plus `time.sleep` backoff, across 7 connected legs. It is
   environment-sensitive (fast NXDOMAIN ≈ 66s/leg; a blackholed connect ≈ deterministic
   >900s), which is why two machines disagreed. Fixed at root with a hang guard that turns
   any control-plane reach into an instant loud assertion, plus a bounded-dispatch wrapper
   and a test that asserts streams terminate with `[DONE]` in bounded wall-clock.
2. **The v1 HTTP-fallback residual was mis-stated.** It was disclosed as "returns
   control-plane JSON without `redaction_hints`." Wrong for **redact** — the control plane
   *does* emit them and the gateway reads them, so I-04 survives that path intact. The
   residual is **rewrite-only**.
3. **A bogus "collection error"** during diagnosis was a shell mistake (`2>&1 > file`
   splits the streams rather than combining them), not a product or test fault.
4. **Three reported §1.5 streaming `500 gateway_internal_error` failures were an artifact
   of my own concurrent editing.** They are not reproducible — 66/66 pass on both the
   working tree and a clean `git worktree` detached at `a0834d3b`. The observation window
   coincided exactly with in-flight edits to `main.py` (the A-01 / A-03 / A-07 fixes).
   The possibility that they were a genuine unhandled exception was nonetheless ruled out
   by direct evidence rather than by assumption: all three failing cells were precisely
   the three that send `extra_body={"routing_preferences": …}`, so that input class was
   attacked with 10 adversarial shapes (string weights, `1e308`, nulls, wrong types,
   unknown sensitivity) × both transports — `10 passed`, no 5xx, parity held.

> **Process lesson, recorded because it produced two false findings.** Running a test
> suite while production files are being edited yields signals that look like product
> defects — first a >900s "hang", then three 500s. Both were reported before they were
> reproducible. In a shared worktree with concurrent sessions, a result must be confirmed
> against a quiesced tree before it is called a defect. A durable mitigation now exists:
> a 5xx body is deliberately empty, so the streaming rig captures the **server-side
> traceback** into the failure message, making the next occurrence self-diagnosing
> (exception type + `file:line`) instead of un-triageable.

---

## 6. Limitations — what this campaign still does not prove

- **Suite interaction beyond the co-run performed.** Streaming was co-run with compat +
  m1_1 + m1_5 (`171 passed`) with singleton snapshot/restore, but the six new suites were
  not exhaustively cross-run against each other.
- **A-05 reachability** is unestablished; it is filed as hardening precisely because no
  provider was shown to emit that shape.
- **CP-01 is open by design decision**, not because it was judged safe.
- **Real vector backends** (Chroma / Pinecone / Milvus) are still faked. Collection ACL and
  namespace isolation are proven against the gateway's enforcement logic, not against a
  live vector store's own semantics.
- **The 70 pre-existing full-suite failures were not investigated.** They predate both
  campaigns and are outside scope; 3 of them are known to encode the *refuted* M17-02
  claim from v1.
- **Adversarial coverage is not exhaustive.** The adversary explicitly flagged the
  streaming output path and cross-tenant as not attacked from its own rig (both are
  covered by sibling suites, but not by an adversary).

---

## 7. Assessment

v1 asked whether ZeroShield's Module 1 controls are real on the stock-SDK path. v2 asked
the harder question — whether v1's *answers* were real — and found that four of them were
not, including one CRITICAL leak in the same channel class v1 had just fixed, and one
regression v1's own fix introduced.

The controls that were genuinely exercised held up well: cross-tenant isolation (20/20),
streaming parity (no gap across five modules), and the CRITICAL I-01 fix on the
previously-untested connected implementation. The failures were concentrated in
**incompleteness of enumeration** — a channel list copied three times, a budget applied at
one of three call sites, a detector reading a proxy for the object it guards. That is the
pattern worth designing against, and the fixes in this round were shaped to make it
structurally harder to repeat.
