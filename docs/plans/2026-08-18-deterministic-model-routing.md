# Deterministic Model Routing — Implementation Plan

**Date:** 2026-08-18
**Branch:** `ansh`
**Goal:** Make `/v1/chat/completions` model routing **100% deterministic** — no LLM anywhere in the
routing decision path — and make the Routing Governance surface (weights, presets, sensitivity)
actually change which model is served.

**Status:** IMPLEMENTED 2026-08-18. Plan confirmed by the operator; D3 migration path **R1**
chosen (flip `FirewallConfig.default_data_sensitivity` default to `public` + rewrite legacy rows).

Two scope additions were requested at confirmation and are included:
1. **Routing configuration is compulsory when adding a model** (frontend + serializer), so a
   catalogue can never again consist of all-default models that score identically.
2. **100+ permutation E2E matrix** across the six compliance frameworks — SOC 2, ISO 27001,
   HIPAA, GDPR, PCI-DSS, NIST.

A seventh root cause (**RC-8**, compliance tags matched case-sensitively) was found during
implementation and fixed; it sat directly in the path of the six-framework requirement.

---

## 1. Operator decisions (locked 2026-08-18)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Adjudicator removal posture | **Delete `adjudicate_model_selection` outright**; rewire both `main.py` call sites to `select_model()` |
| D2 | Scoring normalization | **Min–max per candidate set** |
| D3 | `default_data_sensitivity` | **Wire as a HARD floor** (403 `compliance_routing_unsatisfiable` when unsatisfiable) |
| D4 | E2E test environment | **Reuse the live `zeroshield` org** (11 OpenRouter models) |

> **D3 consequence — ESCALATED 2026-08-18 after control-plane analysis. Read this.**
>
> The problem is larger than the live org. Two Django defaults collide:
> `FirewallConfig.default_data_sensitivity` defaults to **`"internal"`** (`models.py:1310`) while
> `LLMModelConfig.data_sensitivity_level` defaults to **`"public"`** (`models.py:1591`).
>
> Executed `build_compliant_fallback_chains` against an all-`public` catalog — the shipped default
> state — and it returns:
>
> ```
> 'public|'       -> ['gpt-4o-mini', 'gemma']
> 'internal|'     -> []          <-- every default request lands here
> 'confidential|' -> []
> 'restricted|'   -> []
> ```
>
> So **any org that has not run `differentiate_routing_catalog` and has not hand-set per-model
> sensitivity will 403 on every single chat completion** once the hard floor lands — not degrade,
> not narrow: total outage for that org. This is exactly what the in-code `F12 NOTE` at
> `main.py:3264` predicted, now demonstrated rather than theorised.
>
> The live `zeroshield` org survives only by luck: `gpt-5.2` happens to be `internal`, so its
> `internal|` chain is non-empty. Its 10 free models still become unreachable by default, and if
> `gpt-5.2` is isolated, kill-switched, or deactivated, that org 403s too.
>
> **Therefore the hard floor CANNOT ship alone.** It must land together with one of:
> - **(R1, recommended)** migration flipping `FirewallConfig.default_data_sensitivity` default to
>   `"public"` + a data migration rewriting existing `"internal"` rows that were never explicitly
>   set. Preserves current behaviour for everyone; the floor then only bites when an operator
>   deliberately raises it.
> - **(R2)** data migration backfilling `LLMModelConfig.data_sensitivity_level` to `"internal"` for
>   all existing rows. Keeps the `"internal"` default meaningful but silently re-labels every
>   registered model as approved for internal data — a compliance assertion the operator never made.
>
> R1 is the honest one: it does not invent a sensitivity approval on the operator's behalf.
> §7 E-6/E-7/E-8 test all three states (floor satisfiable / unsatisfiable / remedied) and §6 C-1
> pins the empty-chain interaction so it can never be rediscovered in production.

---

## 2. Discovered architecture (evidence-backed)

### 2.1 Request flow

```
POST /v1/chat/completions
  main.py:6885   raw-body re-read  ->  body["routing_preferences"], body["enable_routing"]
  main.py:7002   _extract_chat_routing_preferences(body, org_config, auth_ctx, scan_verdict)
                   -> {routing_enabled, preferred_model, required_compliance,
                       data_sensitivity, latency_budget_ms, estimated_tokens,
                       weights{risk,cost,latency,priority}, request_risk_score,
                       token_budget_tpm, ...}
  main.py:8493   [prefetch path] adjudicate_model_selection(...)   <-- LLM (Bedrock)
                   overlapped with Tier-2 input scan
  main.py:9299   _drop_isolated_or_killed_candidates(...)          <-- kill-switch / model_state
  main.py:9313   [main path]     adjudicate_model_selection(...)   <-- LLM (Bedrock)
  main.py:9330   resolve_runtime_selection(selection)              <-- inactive-model remap
  main.py:9338   _build_routing_metadata(...)                      <-- what the client/UI sees
```

### 2.2 Where the candidate models come from

Control plane `LLMModelConfig` → `post_save` signal → Redis `llm:model_configs:{org_slug}`.
Live payload shape (verified against `ai_mesh_firewall-redis-1`):

```json
{
  "models":  [ { "model_name", "provider", "litellm_params": {model, api_key_encrypted, api_base, custom_llm_provider} } ],
  "routing": [ { "model_name", "model_id", "provider", "data_sensitivity_level", "compliance_tags",
                 "cost_per_1k_input_tokens", "cost_per_1k_output_tokens", "latency_sla_ms",
                 "risk_score", "routing_priority", "rate_limit_rpm", "is_active", "api_key_set" } ],
  "fallback_chains": { "version", "generated_at", "content_hash", "chains": {"public|": [...], "internal|": [...]} }
}
```

The `routing[]` values are auto-derived by `control/ai_mesh_control/core/routing_catalog.py`
(`ROUTING_PROFILES`: `cheap_fast` / `balanced` / `safe_sensitive` / `paid_strong`, matched by
substring on `model_name`, plus a ±10 stable hash offset).

### 2.3 The scoring math (`llm_router.py:1913-1990`)

```python
risk_component     = (1.0 - model_risk) * (1.0 - request_risk_score)
cost_component     = 1.0 / (1.0 + cost_input * estimated_tokens / 1000)
latency_component  = 1.0 if model_latency <= latency_budget_ms else max(budget/model_latency, 0.0)
priority_component = priority / max_priority
composite = w_risk*risk + w_cost*cost + w_latency*latency + w_priority*priority
scored.sort(key=lambda item: item["score"], reverse=True)   # stable -> ties fall to INPUT ORDER
```

---

## 3. Root causes (proven, not suspected)

### RC-1 — Three of four scoring dimensions are inert with real-world data

Ran the exact scorer formulas against the **live** `llm:model_configs:zeroshield` `routing[]` block:

```
=== CURRENT SCORER: winner per preset (LIVE data, 11 candidates) ===
  Balanced           -> gpt-5.2      #2 = poolside/laguna-xs.2:free
  Cost Optimized     -> gpt-5.2      #2 = poolside/laguna-xs.2:free
  Low Latency        -> gpt-5.2      #2 = poolside/laguna-xs.2:free
  Maximum Security   -> gpt-5.2      #2 = poolside/laguna-xs.2:free
  Quality First      -> gpt-5.2      #2 = poolside/laguna-xs.2:free

=== dimension SPREAD across live candidates (max - min) ===
  cost_component       min=0.995021  max=0.999995  SPREAD=0.004974
  latency_component    min=1.000000  max=1.000000  SPREAD=0.000000
  priority_component   min=0.424658  max=1.000000  SPREAD=0.575342
  risk_component       min=0.605000  max=0.845000  SPREAD=0.240000
```

All five Strategy Presets select the **same** model — the most expensive (`$0.01/1k` vs
`$0.00001/1k`, a 1000× gap) and the slowest (6175 ms vs 800 ms) in the catalog.

- **RC-1a `cost_component` asymptotes to 1.0.** `1/(1+c·t/1000)` with `c ∈ [1.0e-05, 1.0e-02]` and
  `t=500` yields `[0.995, 0.999995]`. Cost weight 0.50 × spread 0.005 = **0.0025** of influence,
  versus priority weight 0.15 × spread 0.575 = **0.086**. Priority outvotes cost **34:1** even when
  the operator sets cost to 50%.
- **RC-1b `latency_component` is a dead constant.** Every live SLA (800–6175 ms) is below the
  default 30000 ms budget, so the piecewise branch returns exactly `1.0` for all candidates.
  Measured spread: `0.000000`. "Low Latency" at 55% weight is a **no-op**.
- **RC-1c `request_risk_score` cannot affect ranking.** `(1 - request_risk_score)` is a constant
  factor applied identically to every candidate — it scales all scores, never reorders them.

### RC-2 — The existing tests pass only because the fixture prices are ~1000× inflated

`test_m1_5_routing_governance_sdk.py:32-46`:

```python
def _m(name, mid, provider, risk, cost, sla, prio, sens, tags, key=True): ...
CATALOGUE = [
    _m("gpt-4o-mini",       "gpt-4o-mini",                 "openai",    0.20, 0.15,  800, 5, "public", []),
    _m("claude-3-5-sonnet", "anthropic/claude-3-5-sonnet", "anthropic", 0.05, 3.00, 4000, 9, "restricted", ["hipaa","gdpr"]),
    _m("gemini-1-5-flash",  "google/gemini-1.5-flash",     "google",    0.30, 0.05,  400, 3, "public", ["gdpr"]),
    _m("llama-3-70b",       "meta/llama-3-70b",            "ollama",    0.40, 0.02, 2500, 1, "public", [], key=False),
]
```

`cost_per_1k_input_tokens = 3.00` for Claude Sonnet — the real price is `0.003`. At the fixture's
inflated scale `cost_component` spans `[0.400, 0.990]` and the dimension *looks* alive.
`test_each_weight_dimension_routes_to_its_own_winner` (`:282`) also pins
`latency_budget_ms: 500` to force the latency branch to activate, which never happens under the
30000 ms production default. **The suite is green and the feature is broken.**

**Full fixture audit** (every gateway test carrying `cost_per_1k_input_tokens`):

| File | Cost values (USD/1k) | Realistic? | Why it passes today |
|---|---|---|---|
| `test_m1_5_routing_governance_sdk.py:38-46` | 0.15, 3.00, 0.05, 0.02 | **inflated ~1000×** | inflated spread **and** `latency_budget_ms: 500` (`:278`) |
| `test_v2_streaming_parity_sdk.py:80-86` | 0.15, 3.00, 0.05 | **inflated ~1000×** | same catalogue shape |
| `test_fix_i01_i02_criticals.py:267,276` | 0.15, 3.0 | **inflated ~1000×** | same catalogue shape |
| `test_v3_doc_conformance.py:1224,1228` | 0.01, 3.0 | **3.0 inflated** | mixed scale |
| `test_routing_preferences_differentiation.py:64-97` | 0.00001 – 0.01 | realistic ✓ | but **zeroes every other weight** (`{"risk":0,"cost":1,"latency":0,"priority":0}`, `:108`) — a degenerate single-dimension probe, plus `latency_budget_ms=1000` (`:128`) |
| `test_bedrock_routing.py:120-142` | 0.0003 – 0.0010 | realistic ✓ | narrow spread; pins `latency_budget_ms=1200` (`:298`) |

**Conclusion: not one existing test exercises realistic pricing at realistic preset weights.** The
four inflated fixtures fake a live cost dimension; the two realistic ones only pass by zeroing every
competing weight (which trips the `>= 0.95` weight-lock fastpath) or by pinning an artificially
tight latency budget. That is the complete explanation for a green suite over a broken feature —
and it is why U-2 (all five presets, real prices, default 30000 ms budget) is the single most
important new test in this plan.

Independent confirmation of the weight-share arithmetic, computed against the *production*
`routing_catalog.py` profiles at a realistic mixed vector `{risk .2, cost .1, latency .1,
priority .6}`:

| dimension | raw spread | × weight | share of decidable range |
|---|---|---|---|
| priority | 0.555556 | 0.333333 | **84.64 %** |
| risk | 0.300000 | 0.060000 | 15.23 % |
| cost | 0.004970 | 0.000497 | **0.126 %** |
| latency | 0.000000 | 0.000000 | **0.000 %** |

The 0.004970 cost spread matches the 0.004974 measured against live `zeroshield` by a different
path — two independent derivations, same conclusion.

### ⚠ RC-2b — A third "green test, broken feature" case, found in the *realistic* fixture

`test_routing_preferences_differentiation.py:123` `test_latency_biased_picks_lowest_sla` passes
**by list order, not by latency.** At its pinned `latency_budget_ms=1000`, `cheap-free` (800 ms) and
`fast-balanced` (900 ms) **both** score exactly `1.0` — the piecewise branch cannot separate them.
The assertion at `:131` holds only because `cheap-free` is listed first; reordering the catalogue
returns `fast-balanced` instead. The scorer cannot distinguish 800 ms from 900 ms at *any* budget
≥ 900. So even the file I classified as "realistic ✓" contains a latency test that asserts nothing.

Measured across all nine fixture catalogues at the production default `latency_budget_ms=30000`:
`latency_component` spread is `0.000000` **everywhere**. Exactly one test in the entire suite truly
exercises the latency branch — `test_m1_5_routing_governance_sdk.py:278`, via its artificial 500 ms
budget.

### ⚠ SEQUENCING RULE — rewrite fixture values *after* Phase A, never before

`_score_routing_models` does `round(composite, 4)` (`llm_router.py:1983`) followed by a **stable**
sort (`:1990`), so any 4-decimal tie is resolved by catalogue list order. Rewriting the inflated
fixtures to realistic values while the *current* scorer is still in place therefore breaks tests
that are currently passing. Verified against `test_m1_5`'s catalogue at `cost_weight=1.0`:

```
CURRENT (inflated):  llama-3-70b 0.9901 > gemini 0.9756 > gpt-4o-mini 0.9302 > claude 0.4
                     -> PASSES (test expects llama-3-70b)
realistic rewrite:   gemini 1.0 == llama 1.0  (TIE at 4dp) > gpt-4o-mini 0.9999 > claude 0.9985
                     -> test_m1_5:277 FAILS, winner = gemini by list order
```

Nudging llama to `0.00001` does not help — `round(0.999995, 4) == 1.0`. Under min–max (D2) the
absolute scale stops affecting ordering entirely (verified: inflated and realistic both rank
`llama-3-70b` first), which turns the rewrite into a pure honesty change rather than a behavioural
one. **Phase A must land first.** The plan's step order already does this; this is the reason why.

### RC-3 — The LLM adjudicator is compensating for RC-1

`adjudicate_model_selection` (`llm_router.py:2161-2556`) sends the candidate list, weights, and a
redacted request preview to Bedrock and asks it to pick. It skips itself when a single weight
`>= 0.95` (`ROUTING_WEIGHT_LOCK_THRESHOLD`, `:2211`) — which is exactly the regime the passing
tests exercise, and exactly *not* the regime the UI presets (max 0.60) produce. Removing it without
fixing RC-1 makes routing visibly worse.

### RC-4 — Tie-break is non-deterministic

`scored.sort(...)` is stable, so equal scores resolve to input order, which is Redis sync order.
With all-default `LLMModelConfig` values (`cost=0`, `latency_sla_ms=30000`, `risk_score=0.0`,
`routing_priority=0`) **every candidate ties exactly** and the winner is whatever the control plane
serialized first. Same request, same config → potentially different model after a resync.

### RC-5 — `default_data_sensitivity` is synced but never read

`FirewallConfig.default_data_sensitivity` (`models.py:1311`) reaches the gateway via `config_sync`
but `_extract_chat_routing_preferences` (`main.py:3273`) hardcodes `"public"` as the fallback. The
Routing Governance panel advertises *"Applied when requests don't specify a sensitivity level.
Models below this level are excluded from routing."* — the gateway does neither. Self-documented as
a known gap in the `F12 NOTE` at `main.py:3265`.

### RC-7 — The router drops 8 of 11 live models, so the scorer ranks candidates it cannot serve

**Found 2026-08-18. Independently verified against the running container. This is a prerequisite
for everything else in this plan.**

`_validate_reload_model_entry` (`llm_router.py:617-630`) validates each model with:

```python
validator = getattr(litellm, "get_llm_provider", None)
if callable(validator):
    validator(model=model_id)          # <-- custom_llm_provider NOT passed
```

`_prepare_reload_entry` sets `custom_llm_provider="openai"` for BYOK OpenAI-compatible models
(`llm_router.py:677`, via `normalize_litellm_params`), but the validator ignores it. LiteLLM cannot
resolve bare vendor prefixes, so every OpenRouter model whose slug starts `google/`, `nvidia/`,
`poolside/`, or `liquid/` is rejected at reload.

Live gateway log (`ai_mesh_firewall-gateway-1`):

```
llm_router.py:1778  LiteLLM router hot-reloaded with 3 valid models from Redis (8 invalid dropped)
llm_router.py:1712  Skipping 8 invalid model entries during reload. Sample:
                    google/gemma-4-26b-a4b-it:free: litellm.BadRequestError: LLM Provider NOT provided...
```

Reproduced the fix inside the container:

```
--- WITHOUT custom_llm_provider (current code) ---
  FAIL google/gemma-4-31b-it:free              -> BadRequestError
  FAIL nvidia/nemotron-3-super-120b-a12b:free  -> BadRequestError
  FAIL poolside/laguna-xs.2:free               -> BadRequestError
  FAIL liquid/lfm-2.5-1.2b-instruct:free       -> BadRequestError
--- WITH custom_llm_provider=openai (proposed fix) ---
  OK   google/gemma-4-31b-it:free              -> openai
  OK   nvidia/nemotron-3-super-120b-a12b:free  -> openai
  OK   poolside/laguna-xs.2:free               -> openai
  OK   liquid/lfm-2.5-1.2b-instruct:free       -> openai
```

**Why this breaks determinism even after Phase A.** The scorer's candidate pool comes from
`_filter_inference_eligible_models` (`main.py:4294`), which filters on `is_active` + credentials —
**not** on whether LiteLLM can resolve the provider. So the scorer ranks **all 11** while the router
can only serve **3**. When the winner isn't servable, `resolve_runtime_selection`
(`llm_router.py:507`) → `_resolve_runtime_model` (`llm_router.py:475`) remaps it: first to a
score-ordered `fallback_chain` entry, then the org default, then **`self._active_model_names[0]`** —
input order again, an RC-4 sibling.

Net: for 8 of 11 models the served model is chosen by the *remap*, not by the weights. **Fixing the
scorer without fixing this would leave routing still not weight-driven** — the effective decision
would remain "whichever of the 3 survivors the remap lands on."

Verified live, with the current scorer: `original_model: "openrouter/free"` →
`selected_model: "gpt-5.2"`. That is RC-1 reproducing in production — and `gpt-5.2` is the one model
on a **paid `sk-proj-` OpenAI key**, so every rebias bills real money. See §7 cost note.

### RC-6 — Fallback chain ordering ignores the governance weights entirely

`control/ai_mesh_control/core/routing_fallback.py:45` `_ordered_chain_for_profile` ranks a chain by
`-routing_priority`, then `model_name`. It consumes **none** of the four weights — the control plane
has them on `FirewallConfig` at build time, they are simply never passed to
`build_compliant_fallback_chains`.

Once Phase A lands, primary selection and fallback ordering **disagree by construction**: a
Cost-Optimized org gets a cheap primary and a priority-ranked (i.e. expensive) first fallback. The
`fallback_chain` field is surfaced to operators in `RoutingTechnicalDetails.jsx:63`, so the
inconsistency is visible, not merely internal.

Note also that the *gateway's* own `fallback_chain` (`ModelSelection.fallback_chain`, the next 3
scored candidates) is weight-ranked and therefore correct — it is the *control-plane* Redis
`fallback_chains` block used by `routing_isolation.py:133-140` that is priority-only. Two different
orderings for the same concept. **See Open Question OQ-1.**

---

## 4. Non-goals (explicitly out of scope)

- Tier-1 / Tier-2 **security scanning** stays LLM-backed (Bedrock). This plan removes the LLM from
  the *routing decision* only, not from threat detection.
- `output_guard`, `llm_judge`, `intent_classifier`, hallucination grounding — untouched.
- The kill-switch / `model_state` isolation reroute path (`decision_source: kill_switch` /
  `model_state`) is already deterministic and stays as-is.
- No changes to `routing_catalog.py` profile values (the scorer fix makes the existing spread
  sufficient; re-tuning profiles would confound the before/after comparison).

---

## 5. Implementation

### Phase A0 — PREREQUISITE: stop dropping OpenAI-compatible BYOK models (RC-7)

Without this, the scorer keeps ranking candidates the router cannot serve and the remap keeps making
the real decision — Phase A would be unobservable.

**A0-1.** `llm_router.py:625-626` — pass the provider hint through:
```python
validator(model=model_id,
          custom_llm_provider=(params or {}).get("custom_llm_provider") or None)
```
Empirically verified to resolve all four previously-failing slugs.

**A0-2.** Close the scoring/serving gap at its root: the scorer's candidate pool must not contain
models the router cannot serve. Add a router-servability check to
`_filter_inference_eligible_models` (`main.py:4294`), or have the reload publish its dropped set so
scoring can exclude them. A model that cannot be served should never win a routing decision and then
get silently remapped.

**A0-3.** Make the drop **loud**. Today 8 of 11 models vanish behind a single WARNING line. Emit an
operational event / telemetry counter so an operator sees "8 of your 11 models are unroutable"
rather than discovering it in a log grep.

**A0-4.** `_resolve_runtime_model`'s final fallback is `self._active_model_names[0]`
(`llm_router.py:504`) — input-order dependent, same class of bug as RC-4. Give it the same explicit
tie-break ordering as Phase A4.

**Tests:** U-18 `test_openai_compatible_byok_models_survive_reload` (all 11 live-shaped entries
survive `_filter_valid_reload_models`); U-19 `test_scorer_never_ranks_unservable_model`.

### Phase A — Deterministic scorer core (`gateway/ai_mesh_gateway/llm_router.py`)

**A1. Min–max normalize every dimension across the candidate set** — rewrite the scoring loop in
`_score_routing_models` (`:1953-1990`):

```python
# span helpers computed ONCE over `eligible`
def _norm(value, lo, hi, *, invert):
    if hi - lo <= _SPAN_EPSILON:      # degenerate: all candidates equal -> dimension carries no signal
        return 1.0
    t = (value - lo) / (hi - lo)
    return 1.0 - t if invert else t

cost_component     = _norm(cost_in,   c_lo, c_hi, invert=True)    # cheapest -> 1.0
latency_component  = _norm(sla_ms,    l_lo, l_hi, invert=True)    # fastest  -> 1.0
risk_component     = _norm(model_risk, r_lo, r_hi, invert=True)   # safest   -> 1.0
priority_component = _norm(priority,  p_lo, p_hi, invert=False)   # highest  -> 1.0
```

Rationale for `1.0` on a degenerate span (rather than `0.0` or `0.5`): a dimension where every
candidate is identical must not penalise anyone, and must not silently shrink the composite score
relative to an org whose catalog happens to be diverse.

**A2. Keep `latency_budget_ms` as a hard constraint, separate from the soft score.** Today the
budget is fused into the score. Split them:
- Candidates with `latency_sla_ms > latency_budget_ms` are **filtered out** (hard), consistent with
  how compliance is treated.
- If that empties the pool, fall back to scoring all candidates and flag
  `latency_budget_unsatisfiable` in `decision_factors` (mirrors the existing sensitivity soft-fallback
  pattern, so a tight budget degrades honestly instead of 503ing).
- The *soft* latency dimension is then pure min–max over the survivors.

**A3. `request_risk_score` becomes a real risk *floor*, not a constant multiplier.** Replace
`(1-model_risk)*(1-request_risk)` with:
- `risk_component` = min–max over `model_risk` (A1), and
- when `request_risk_score >= RISK_ESCALATION_FLOOR` (default 0.50), candidates whose
  `risk_score > (1 - request_risk_score)` are hard-filtered — a high-risk prompt cannot be served by
  a high-risk model. Emits `risk_escalation_applied` in `decision_factors`.
- Same soft-fallback rule as A2 if the filter empties the pool.

**A4. Deterministic total ordering.** Replace the bare score sort with an explicit, fully-specified
key so no two candidates can ever be order-ambiguous:

```python
scored.sort(key=lambda i: (
    -round(i["score"], 6),                     # 1. composite score, desc (rounded: float noise != a real difference)
    -i["model"].get("routing_priority", 0),    # 2. higher operator priority wins
     float(i["model"].get("cost_per_1k_input_tokens", 0.0)),   # 3. cheaper wins
     int(i["model"].get("latency_sla_ms", 30000)),             # 4. faster wins
     i["model_name"],                          # 5. lexicographic — the final, total tie-break
))
```

`score_tie` reporting (`_scores_tied`, `_SCORE_TIE_EPSILON`) is retained for transparency —
the UI still shows that a tie occurred, it is just no longer resolved by luck.

**A5. Selection metadata.** `select_model` returns
`decision_source="deterministic_weighted"`, `evaluator_model=""`, and a
`policy_summary` naming the dominant dimension. `candidate_scores` gains the normalized
per-dimension values so `RoutingTechnicalDetails` can show *why*.

### Phase B — Delete the LLM adjudicator

> **Two hazards identified 2026-08-18 by gateway-path analysis. Both are silent failures.**

**⚠ HAZARD B-H1 — deleting the overlap machinery can silently disable the Tier-2 input scan.**
The Tier-2 scan coroutine is *created* at `main.py:8461-8469` and the **`overlap=False` branch of
`_maybe_overlap_t2_and_routing` is the only place it is ever awaited.** Removing the helper without
re-awaiting `_t2_scan_coro` skips the entire Tier-2 input scan — a **fail-OPEN on the security
path** that surfaces only as a `RuntimeWarning`, not an error. Mitigation: either keep the helper and
always pass `overlap=False`, or inline `verdict = await _t2_scan_coro`. A plain `await` propagates
`Tier2UnavailableStrict` exactly as `gather(return_exceptions=False)` did, so the 451 fail-closed
contract at `main.py:8544` survives either way. **Test U-15 below is the guard.**

**⚠ HAZARD B-H2 — `select_model` never sets `requested_model`, breaking Requested≠Served honesty.**
`adjudicate_model_selection` set `heuristic.requested_model = preferred_model or "auto"` on every
return path. `select_model` does not accept `preferred_model` at all, and leaves `requested_model`
at its `""` dataclass default. That field is load-bearing in four places:
`_build_routing_metadata` → `original_model` + the `rerouted` computation (`main.py:3535-3540`);
`X-ZeroShield-Rerouted` / `X-ZeroShield-Original-Model` (`main.py:11035, 11038`); telemetry
`action = "reroute"|"confirm"` (`main.py:9366`); stream headers
(`stream_orchestration.py:221, 225-227`). Without a fix, **every client-pinned request reports
`original_model="auto"` and `rerouted=false`.** Mitigation: extend `select_model` to accept
`preferred_model` and set `requested_model` itself (preferred over patching at the call site, so
both call sites and any future caller get it). **Test U-16 below is the guard.**

**B1.** Delete `LLMRouter.adjudicate_model_selection` (`llm_router.py:2161-2556`, ~395 lines).

**B2.** Extend `select_model` with `preferred_model: str = ""` (sets `requested_model`, per B-H2)
and `token_budget_tpm: int | None = None` (carried into metadata only, not scored). Rewire
`main.py:9313` to a direct `LLM_ROUTER.select_model(...)` call. Preserve `resolve_runtime_selection`
at `main.py:9330` (pure/local) and `_drop_isolated_or_killed_candidates` at `main.py:9298`
(authoritative isolation filter). Preserve the `_client_sent_routing_sentinel` override at
`main.py:9333`.

**B3.** Delete the prefetch/overlap block. Confirmed dead once the adjudicator is gone:
`_prefetch_adjudicate()` (`main.py:8485-8509`), `_should_overlap_input_t2_and_routing`
(`main.py:3430-3449` + call `:8471-8477`), `_accept_prefetched_route` (`main.py:3463-3487` + call
`:9301-9311`), `_routing_candidate_ids` (`main.py:3452-3461`), `_PREFETCH_RISK_DELTA`
(`main.py:3427`), the `prefetched_route_selection` / `_prefetch_risk` / `_prefetch_candidate_ids`
locals (`main.py:6984-6986`, `:8511-8517`), `_overlap_t2_route` / `_prefetch_box`
(`main.py:8471-8479`), the duplicate `_drop_isolated_or_killed_candidates` call inside the prefetch
(`main.py:8482-8484`), and the `if _accept_prefetched_route(...) / else` fork (`main.py:9301-9312`).

**MUST survive B3** (all downstream of the Tier-2 await, unrelated to routing): the
`_is_tier2_unavailable_strict` → 451 + `Retry-After` handler (`main.py:8544`); the
`tier2_ms`/`tier1_ms` split (`main.py:8519-8523`, keyed off `verdict.tier`, not overlap); and the
`else:` branch at `main.py:8524-8543` (`force_sync_tier2` false → `scan_prompt` +
`enqueue_job("tier2_post_scan")`).

**B4.** Remove now-dead env/config: `ROUTING_ADJUDICATOR_ALWAYS`, `ROUTING_ADJUDICATOR_RISK_FLOOR`,
`ROUTING_WEIGHT_LOCK_THRESHOLD` (`llm_router.py:2204-2206`), `BEDROCK_ADJUDICATOR_MODEL`,
`BEDROCK_ADJUDICATOR_MAX_TOKENS` (`llm_router.py:2364`), the
`resolve_platform_bedrock_model("adjudicator", ...)` usage, and the `ROUTING_ADJUDICATOR_ALWAYS`
entries in `docker-compose.yml` / `docker-compose.prod.yml`.

**B5.** `decision_source` must be set to `deterministic_weighted` **inside `select_model`** — its
current success value is the bare string `"weighted"`, which `routingExplain.js:167` lumps in with
`policy_adjudicator` and renders as *"The Policy Adjudicator selected X"*. Leaving it as `"weighted"`
would emit a false claim about a component that no longer exists.

**Confirmed out of scope** (verified: not in the routing decision path): `llm_judge.py` and
`intent_classifier.py` are injected only into `RAGFirewallPipeline` (`main.py:6039`, `:6042`) and are
never referenced in `proxy_chat`, which uses the regex-only `scanner.classify_intent`
(`main.py:8063`). `bedrock_scanner` (`call_site="tier2_scan"`) and `output_guard`
(`call_site="output_rewrite"`) are scan/output path. `_rewrite_output_response_text` already pins
`{"enable_routing": False}` (`main.py:4703`) so it cannot recurse into routing.

**Note:** `/v1/responses` (`main.py:11511`) and `/v1/completions` (`main.py:16306`) re-enter
`proxy_chat` via `_dispatch_chat_internally` (`main.py:11291`) — they inherit all of the above and
need no separate work.

**Decision-source vocabulary after this change** (`decision_source` is displayed by the UI):

| Value | When |
|---|---|
| `deterministic_weighted` | normal deterministic selection (replaces `weighted`, `weighted_fastpath`, `policy_adjudicator`, `weighted_fallback`) |
| `compliance_block` | unchanged — no candidate satisfies compliance/sensitivity → 403 |
| `kill_switch` / `model_state` | unchanged — isolation reroute |

### Phase C — Wire `default_data_sensitivity` as a hard floor (D3)

**C1.** `_extract_chat_routing_preferences` (`main.py:3273`): resolution order becomes
`routing_preferences.data_sensitivity` → `metadata.data_sensitivity` → `body.data_sensitivity` →
**`org_config["default_data_sensitivity"]`** → `"public"`. Replace the `F12 NOTE` with a comment
recording the operator decision and its consequence.
**C2.** Remove the sensitivity **soft-fallback** in `_score_with_sensitivity_fallback`
(`llm_router.py:1993-2045`) so an unsatisfiable floor returns `[]` and the existing
`_routing_compliance_required` gate at `main.py:9377` produces
**403 `compliance_routing_unsatisfiable`** — matching the UI copy *"Models below this level are
excluded from routing."*
**C3.** `_routing_compliance_required` must now treat an org-defaulted (not just client-supplied)
non-public sensitivity as compliance-required, otherwise the 403 gate is skipped and the request
would fall through to the client's original model — a fail-open.
**C4.** Add `data_sensitivity_source: "request" | "org_default"` to `_build_routing_metadata` so an
operator can tell from the trace whether the floor came from the caller or from their own config.

### Phase D — Control plane + config sync

**D1.** `config_sync.py:79` — confirm `default_data_sensitivity` is in the synced key set
(`routing_priority_weight` is; verify sensitivity is too) and that a Routing Governance save
invalidates the gateway cache promptly.
**D2.** `FirewallConfig` weight validators: individual `[0,1]` bounds exist but **no Σ=1.0
constraint**. The gateway normalizes anyway (`_normalize_weights`), so add a serializer-level
*warning* field rather than a hard validation error — rejecting a save would break the panel's
free-slider UX.
**D3.** No migration required — no schema change.

### Phase E — Frontend alignment

> **Revised 2026-08-18 after frontend analysis.** The collapse to `deterministic_weighted` crashes
> nothing and blanks nothing — every unrecognized-value fallback is safe. The real damage is that
> **two paths render confidently WRONG content.** Blank would have been safer.

**E1 — `RoutingAuditPanel.jsx:224` is the most damaging line in the whole change. MUST FIX.**

```jsx
{decisionSource === "policy_adjudicator" ? "Policy adjudicated" : "Weighted fallback"}
```

A hard binary with no third state: **anything** that isn't the literal `"policy_adjudicator"` renders
the badge **"Weighted fallback"** — affirmatively telling the operator the adjudicator was unavailable
and the system degraded. `deterministic_weighted` hits the else. It does not blank; **it lies, on the
primary audit surface.** Replace with `formatDecisionSource(decisionSource)`, and change the `:162`
default off `"weighted"`.

**E2 — `routingExplain.js:177-180` catch-all silently guts the explanation. MUST FIX.**
The `because` clause is computed at `:121-132` (`topDifferentiators` at `:43-63` ranks components by
`score × weight`, plus sensitivity/tie/remap hints) and then **never appended** in the catch-all
branch. Output degrades to the raw first line of `routing_reason` or bare `"Routed to auto."`. The
discarded text is *"it ranked best on cost and response time"* — precisely the per-dimension
explanation a deterministic weighted router should be surfacing. Add `deterministic_weighted` to the
`:167` branch (which keeps `${because}`); delete the now-dead `:159` `weighted_fastpath` and `:163`
`weighted_fallback` branches.

**E3.** `constants/zeroshieldBrand.js:32-46` — add an explicit `deterministic_weighted` label entry.
Without it `formatDecisionSource` (`:64-67`) auto-title-cases to "Deterministic Weighted", which is
survivable but not real copy.

**E4 — `candidate_scores` is carried but rendered nowhere.** `RoutingTechnicalDetails.jsx` renders
only 7 fields; `candidate_scores`, `decision_source`, `sensitivity_fallback`, `score_tie`, and
`remapped_from` are all populated and never displayed. **Gotcha:** `routingHasTechnicalDetails`
(`routingExplain.js:186-196`) gates the whole component (`RoutingTechnicalDetails.jsx:7` →
`return null`) and does **not** test `candidate_scores` — so adding per-dimension rendering without
also adding `candidate_scores` to that predicate means an event whose only populated field is
`candidate_scores` hides the entire panel.

**E5 — weight-key mismatch to watch.** `weightKeyForComponent` (`routingExplain.js:38-40`) strips
`_component` and expects **short** keys `risk|cost|latency|priority`, not the `routing_*_weight`
names the governance panel persists. `ModelRoutingSimulator.jsx:98-101` also sends the short form
(and maps `quality_weight` → `priority_weight`). Keep the gateway emitting short keys in
`route_metadata.weights`.

**E6 — copy rewording.** Load-bearing strings: `RoutingGovernancePanel.jsx:258` ("Controls whether
ZeroShield **policy adjudication** runs…"), `:396` (the "ZeroShield Policy Adjudicator analyzes…"
paragraph), and `howToUseContent.js:666` (documents the exact `"policy_adjudicator" or
"weighted_fallback"` enum being replaced), `:671`, `:730` (sample payload), `:797` (explicitly claims
*"The model analyzes input content"* — must change, it will no longer be true).
Also fix the pre-existing lie at `howToUseContent.js:651`: it tells operators the weights
*"auto-normalize to 100%"* — the frontend does no such thing (confirmed: no client-side
normalization anywhere; only the gateway normalizes).
Preset weights (`:29-35`) stay **unchanged** — all five already sum to exactly 1.00.

**E7.** Keep `kill_switch`, `model_state`, `routing_disabled` **out of the collapse** — they carry
semantics available in no other field, and `liveGateway.js:859` matches on
`decision_source === "kill_switch"` to detect a kill-switch stage reroute. If that stops matching
and `trigger_source` is also absent, the stage silently falls to `"allow"` with detail *"No active
kill-switch for this model"* while a reroute actually happened.

**E8.** `evaluator_model` → empty string is **completely safe**: two producers, both already
defaulting to `""` (`liveGateway.js:929`, `pipelineTrace.js:373`), and **zero consumers** anywhere
in the frontend.

**E9.** Add a warning affordance in the panel when Default Data Sensitivity is set above the
sensitivity of every registered model (the D3 footgun) — read from the existing models list.

**E10 — do NOT delete the Bedrock scrubbing layer.** `zeroshieldBrand.js:23` (`"bedrock adjudicator"`
→ label) and the `:28` haiku-model regex become dead for the routing path but still guard the Tier-2
scanner copy. Leave them.

---

## 6. Unit / component tests

| ID | Test | Asserts |
|----|------|---------|
| U-1 | `test_no_llm_in_routing_path` | Patch `bedrock_client.default_bedrock_client` to raise on any call; a full `/v1/chat/completions` routing decision still succeeds. **The regression guard for "deterministic".** |
| U-2 | `test_presets_select_distinct_models_realistic_pricing` | All 5 UI presets against a catalog using **real USD/1k** values (`0.000019`…`0.003`) select the expected distinct winners. Directly kills RC-1/RC-2. |
| U-3 | `test_cost_dimension_moves_the_winner` | `cost_weight=0.50` picks the cheapest; flipping to `priority_weight=0.50` picks a different model. |
| U-4 | `test_latency_dimension_alive_at_default_budget` | With `latency_budget_ms=30000` (production default, **not** 500) `latency_weight=0.55` picks the lowest-SLA model. |
| U-5 | `test_identical_scores_break_deterministically` | Two candidates with byte-identical routing attrs → winner is the lexicographically smaller `model_name`; stable across 100 shuffles of the input list. |
| U-6 | `test_all_default_catalog_is_deterministic` | All-default `LLMModelConfig` values (the RC-4 case) → same winner across 100 input permutations. |
| U-7 | `test_degenerate_span_does_not_penalize` | Single candidate, and N identical candidates → score is well-formed, no div-by-zero, no NaN. |
| U-8 | `test_org_default_sensitivity_is_applied` | Request omits `data_sensitivity`, org default `internal` → only `internal`+ models are eligible; metadata carries `data_sensitivity_source: "org_default"`. |
| U-9 | `test_org_default_sensitivity_unsatisfiable_403` | Org default `restricted`, all models `public` → **403 `compliance_routing_unsatisfiable`**, and the request is **not** served by the original model (fail-closed). |
| U-10 | `test_latency_budget_unsatisfiable_soft_fallback` | Budget below every SLA → best-available + `latency_budget_unsatisfiable` factor, not a 503. |
| U-11 | `test_high_request_risk_filters_risky_models` | `request_risk_score=0.9` excludes `risk_score > 0.1` candidates; emits `risk_escalation_applied`. |
| U-12 | `test_weights_all_zero_is_stable` | Σ=0 → defaults restored (existing R2-RT-3 guard) and selection still deterministic. |
| U-13 | `test_negative_and_nan_weights_clamped` | Existing clamp behaviour preserved post-refactor. |
| U-14 | `test_decision_source_is_deterministic_weighted` | No response, trace, or telemetry row ever carries `policy_adjudicator`. |
| **U-15** | `test_tier2_input_scan_still_runs_after_overlap_removal` | **Guard for HAZARD B-H1.** Spy on `scan_prompt_with_tier2`; assert it is awaited exactly once on a `force_sync_tier2` request, and that `Tier2UnavailableStrict` still yields 451 + `Retry-After`. Without this, deleting the overlap block silently disables Tier-2 input scanning. |
| **U-16** | `test_requested_model_survives_on_pinned_request` | **Guard for HAZARD B-H2.** Client pins `model="gpt-4o-mini"`, routing selects something else → `original_model == "gpt-4o-mini"`, `rerouted == true`, `X-ZeroShield-Rerouted: true`, telemetry `action == "reroute"`. And with `model="auto"` → `original_model == "auto"`, `action == "confirm"`. |
| U-17 | `test_isolation_filter_still_applied_once` | `_drop_isolated_or_killed_candidates` is called exactly once (the duplicate inside the deleted prefetch is gone, the authoritative one at `main.py:9298` remains). |

### ⚠ Pre-existing baseline — `ansh` has 26 failures TODAY, before any change

Measured full suite on the current branch: **26 failed, 4424 passed, 56 skipped, 25 xfailed,
3 xpassed**, 181 s wall.

| File | Failures |
|---|---|
| `test_v2_tier2_detection_sdk.py` | 15 |
| `test_v2_connected_path_sdk.py` | 7 |
| `test_input_detail_masking.py` | 3 |
| `test_fix_i01_i02_criticals.py::test_i02_genuinely_standalone_deployment_still_serves` | 1 |

All 26 are Tier-2 scan / masking — **none are routing**. Phase 0 must capture this baseline
verbatim so the refactor is not blamed for them. Note the `test_fix_i01_i02` one is also a `gov`
fixture consumer, so under the refactor it changes failure *mode* (fail → error) without being a
new regression.

### Measured breakage, precise

**7 genuine behavioural failures · ~39 fixture-wiring errors · 5 stale literals.**

The 39 errors are almost all one line, repeated across files — e.g.
`test_m1_5_routing_governance_sdk.py:146`:
```python
lr.adjudicate_model_selection = real.adjudicate_model_selection   # RHS raises once deleted
```
→ `lr.select_model = real.select_model`. Same pattern at `test_v3_doc_conformance.py:1260`
(10 cases) and `test_fix_i01_i02_criticals.py:342` (4 cases). `test_m1_5` is already behaviourally
adjudicator-free (`:163` sets `ROUTING_ADJUDICATOR_ALWAYS=false`), so rewiring alone fixes all 25.

The 7 real failures: `test_bedrock_routing.py` ×4 (the file's purpose is gone — but
`test_platform_model_rejected_from_litellm_resolve:369` **survives**; move it before deleting the
rest) and `test_routing_preferences_differentiation.py` ×3. Of those,
`test_adjudicator_always_invokes_on_balanced_weights:211` is the hardest blocker — it is the
explicit contract asserting that balanced weights *must* reach the LLM, which is precisely the
behaviour being removed.

**⚠ Import-level caveat:** if Phase E deletes `ZEROSHIELD_ADJUDICATOR_LABEL` or
`_sanitize_routing_reason`, the module-level import at `test_pipeline_trace_routing.py:3-9` fails and
**all 5 tests error** rather than 4 going stale. Keep the symbols or update the import in the same
commit.

**Consolidation:** there is **no** `gateway/ai_mesh_gateway/tests/conftest.py`, and
`conftest.py` has no routing catalogue — every fixture is inlined per file, which is exactly how the
inflated `gpt-4o-mini @0.15 / claude-3-5-sonnet @3.00` pair propagated by copy-paste into four
files. Lift one canonical catalogue into a shared fixture as part of the rewrite so the next value
drift cannot happen four ways.

**Existing tests to update** (blast radius, D1):

| File | Tests | Change |
|---|---|---|
| **`test_t2_routing_overlap.py`** | **18** | **Missed in the first pass.** ~20 references to the deleted prefetch/overlap surface; most of the file goes. Salvage any test that asserts Tier-2 fail-closed behaviour and fold it into U-15. |
| `test_bedrock_routing.py` | 5 | Adjudicator-specific — delete or rewrite as deterministic equivalents |
| `test_pipeline_trace_routing.py` | 5 | 4 `policy_adjudicator` literals → `deterministic_weighted` |
| `test_routing_preferences_differentiation.py` | 10 | `weighted_fastpath` / `policy_adjudicator` → `deterministic_weighted`; **raise fixture prices to realistic values** |
| `test_m1_5_routing_governance_sdk.py` | 25 | **Fix the 1000×-inflated `CATALOGUE` prices (RC-2)**; drop the artificial `latency_budget_ms: 500` |
| `test_pipeline_stage_transparency.py` | 6 | 2 `policy_adjudicator` literals |
| `test_v3_doc_conformance.py` | 93 | verify `decision_source` doc conformance still holds |
| `test_stream_orchestration.py` | — | 2 `decision_source = "weighted"` literals |
| `test_fix_i01_i02_criticals.py` | 15 | verify no adjudicator coupling |

**Control-plane tests** (`control/ai_mesh_control/core/tests/`, needs Postgres — see session notes):

| File | Change |
|---|---|
| `test_routing_telemetry_metadata.py` | Fixtures `decision_source: "weighted"` → `deterministic_weighted`; asserts `routing_reason` / `policy_summary` / `decision_source` provenance |
| `test_routing_fallback.py` | Only if OQ-1 changes chain ordering or bumps `FALLBACK_CHAIN_SCHEMA_VERSION` |
| `test_routing_catalog.py` | Safe unless the D3 remedy retunes `ROUTING_PROFILES` sensitivity lanes — `test_lanes_differ_on_cost_and_sensitivity` hard-asserts `cheap == "public"`, `safe == "confidential"` |
| **C-1 (new)** | `build_compliant_fallback_chains` over an all-`public` catalog yields an **empty** `internal\|` chain — pins the D3 collision so it can never be rediscovered in prod |
| **C-2 (new)** | `PUT /api/firewall/config/` with routing fields — **currently zero coverage**: no test exercises any routing field through the API, nor `build_gateway_payload()` routing keys, nor weight range/NaN rejection |

**Frontend tests** (`npm run test:unit`, `node:test`):

| File | Change |
|---|---|
| `utils/routingExplain.test.js` | **3 of 6 fail immediately**: `:30` (adjudicator reroute, asserts `/ZeroShield Policy Adjudicator/`), `:79` (`weighted_fallback`, asserts `/unavailable/`), `:90` (asserts `/best fit among 4 eligible models/`). `:43`/`:56`/`:66` unaffected. |
| `constants/zeroshieldBrand.test.js:47-52` | Label rename |
| `utils/pipelineTrace.test.js:242-272` | Passes — uses `policy_adjudicator` as input but never asserts on it |
| **F-1 (new)** | `RoutingAuditPanel` badge renders the real source, not "Weighted fallback" — **nothing currently catches the E1 regression**; there are zero `.test.jsx` files in the repo |

**Commands (measured, not guessed).** Setup once: `cd gateway && uv sync --extra dev`.
Config is in `gateway/pyproject.toml:41-46` (`asyncio_mode = "auto"`); there is no Makefile target
for the gateway suite.

Routing-only — **3.0 s, 75 passed / 1 xfailed** on the current branch:
```bash
cd gateway && ./.venv/bin/python -m pytest \
  ai_mesh_gateway/tests/test_m1_5_routing_governance_sdk.py \
  ai_mesh_gateway/tests/test_routing_preferences_differentiation.py \
  ai_mesh_gateway/tests/test_bedrock_routing.py \
  ai_mesh_gateway/tests/test_routing_isolation.py \
  ai_mesh_gateway/tests/test_pipeline_trace_routing.py \
  ai_mesh_gateway/tests/test_t2_routing_overlap.py \
  ai_mesh_gateway/tests/test_simulator_model_selection.py \
  ai_mesh_gateway/tests/test_model_isolation_verification.py \
  -q -p no:cacheprovider
```
Embedded routing tests in larger files:
```bash
cd gateway && ./.venv/bin/python -m pytest \
  ai_mesh_gateway/tests/test_v3_doc_conformance.py \
  ai_mesh_gateway/tests/test_v2_streaming_parity_sdk.py \
  ai_mesh_gateway/tests/test_fix_i01_i02_criticals.py \
  ai_mesh_gateway/tests/test_config.py \
  ai_mesh_gateway/tests/test_pipeline_stage_transparency.py \
  ai_mesh_gateway/tests/test_stream_orchestration.py \
  -k "rout or adjudic or sensitiv or test_15" -q -p no:cacheprovider
```
Full gateway suite before handover — **181 s wall**:
`cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/ -q -p no:cacheprovider`

Control (needs Postgres container `ai_mesh_firewall-postgres-1`):
```bash
cd control/ai_mesh_control && DATABASE_URL=postgres://ai_mesh_firewall:ai_mesh_firewall@localhost:5432/ai_mesh_firewall \
  DEBUG=True DJANGO_SECRET_KEY=dev python manage.py test \
  core.tests.test_routing_catalog core.tests.test_routing_fallback core.tests.test_routing_telemetry_metadata
```
Frontend: `cd frontend && node --test src/utils/routingExplain.test.js` (full: `npm run test:unit`)

---

## 6b. Open questions surfaced during exploration

**OQ-1 — Should control-plane fallback chains be re-ranked by the governance weights?** (RC-6)
Doing it properly means passing the four `FirewallConfig` weights into
`build_compliant_fallback_chains` and bumping `FALLBACK_CHAIN_SCHEMA_VERSION` (currently `1`) so the
gateway has a clean discriminator. **My recommendation: yes, but as a follow-up PR** — it is a
control-plane change with its own migration/versioning concern, and bundling it would make the
before/after comparison for the scorer fix harder to read. Chains are currently only consumed on the
isolation-reroute path, so the inconsistency is bounded. Say the word if you want it in scope now.

**OQ-2 — Weight-change audit trail.** `PUT /api/firewall/config/` is gated on bare
`IsAuthenticated` with no role check and emits no `AuditLog` row (only a `logger.info` at
`firewall_config_views.py:71`), while `LLMModelConfig` writes do emit audit rows. So any org member
can silently rewrite the routing weights, flip `routing_enabled`, or change the sensitivity floor.
Out of scope for this plan; flagging because the sensitivity floor is about to become a hard
security control rather than a hint.

**OQ-3 — `LLMModelConfig.risk_score` is not a live signal.** Its help_text says *"Updated from
telemetry"*; it never is. `tasks.py:1199,1248` update `GatewayAPIKey.risk_score`, and
`ModelState.risk_score` is a separate 0–100-scale field. Routing `risk_score` is only ever set via
the REST API or `differentiate_routing_catalog`. The Phase A risk dimension is therefore operator
configuration, not observed behaviour — correct as designed, but worth knowing before anyone reads
"Risk Avoidance 55%" as adaptive.

---

## 7. End-to-end validation (mandatory — unit tests are NOT sufficient)

Environment: **live local stack** (already up) — gateway `:8300`, control `:8100`, frontend `:8180`,
redis, postgres. Org `zeroshield` (id 2), 11 registered OpenRouter models — **but only 3 routable
until Phase A0 lands** (RC-7). OpenRouter key verified: 413 models reachable,
`openai/gpt-4o-mini` returns `PONG`.

**Harness: extend `gateway/ai_mesh_gateway/tests/test_v4_live_e2e.py`** — do not write a new one.
(`gateway/scripts/` contains only `bedrock_latency_probe.py`.) It already drives the unmodified
`openai` SDK over real TCP with `enforcement_mode=block`, and skips cleanly when credentials are
absent. Auth: `GATEWAY_API_KEY` env, else `TEST_EMAIL`+`TEST_PASSWORD` → JWT → `simulator-default`.
Run: `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_v4_live_e2e.py -v`

Its docstring carries a warning worth heeding: a prior campaign filed three phantom product gaps
because the deployed gateway image **lagged the source tree**. **Every E2E run below must first
confirm the running container matches HEAD** — otherwise a "fixed" behaviour may simply not be
deployed. This is also recorded in memory as a repeat failure mode for this repo.

### Pre-flight (blocking, before any E2E run)

| # | Step | Why |
|---|---|---|
| P-1 | Land Phase A0 and confirm the reload log reads `11 valid models … 0 invalid dropped` | Without it only 3 models are servable and the preset matrix cannot differentiate |
| P-2 | Rebuild the gateway container from HEAD and verify the image SHA | Guards the phantom-gap failure mode above |
| P-3 | Confirm `DJANGO_SECRET_KEY` fingerprint matches across gateway and control (`sha256 \| cut -c1-8`) | `ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY` is unset; the whole BYOK decrypt path rides the `DJANGO_SECRET_KEY` fallback. A mismatch makes every key silently decrypt to `""` (`llm_model_crypto.py:69` swallows `InvalidToken`) |
| P-4 | Decide the `firewall:config:zeroshield` question — see below | The first config PUT is a permanent state change |

### ⚠ Two operational constraints discovered

**Cost.** Verified live: a request for `openrouter/free` was rebiased to `gpt-5.2`, which is the one
model on a **paid `sk-proj-` OpenAI key**. That is RC-1 reproducing in production. **Until A0 + Phase
A land, every E2E request bills the paid model.** Sequence the work so bulk E2E traffic (E-1 ×20,
E-17 ×50) runs only *after* the scorer fix, or the run costs real money for no signal.

**Org `zeroshield` has NO `FirewallConfig` row.** Redis holds only `firewall:config:testing` and
`firewall:config:default`; the org resolves through `default` (which is where its
`default_data_sensitivity: "internal"` actually comes from). **The first `PUT /api/firewall/config/`
creates the row and permanently detaches `zeroshield` from `default`** (`firewall_config_views.py:44`
get_or_creates). Fine for testing, but it must be a deliberate act, not a side effect of E-4.

**E-12 is not runnable as originally written.** Bedrock unreachable does **not** degrade to Tier-1 —
`org_tier2_strict` defaults `True` (`main.py:8387`) and the live default config has it `True`, so
once the breaker opens (~5 failed scans in 60 s, then 30 s cooldown —
`bedrock_tier2_breaker.py:32`) every request returns **HTTP 503 `tier2_unavailable`** and dies before
routing. To make the test prove anything, set `ENABLE_TIER2=false` (so `_bedrock_scanner` is never
constructed and Tier-1 returns immediately) **before** blocking egress. Note the adjudicator half
already fails open (`llm_router.py:2405`) — but that path is being deleted anyway.

| ID | Scenario | Pass criterion |
|----|----------|----------------|
| E-1 | **Determinism** — identical request ×20 | Identical `selected_model` every time; zero variance |
| E-2 | **Determinism across restart** — 10 calls, `docker restart gateway`, 10 more | Same winner before and after |
| E-3 | **Determinism across resync** — 10 calls, force control-plane config resync (reorders Redis `routing[]`), 10 more | Same winner (proves RC-4 fixed) |
| E-4 | **Preset matrix** — all 5 presets × live catalog, saved through the real Routing Governance API | ≥3 distinct winners; `Cost Optimized` → cheapest; `Low Latency` → lowest SLA; `Quality First` → highest priority |
| E-5 | **Per-request override** — `routing_preferences.weights` in `extra_body` | Overrides org config; deterministic per weight set |
| E-6 | **Sensitivity hard floor (D3)** — org default `internal`, request omits sensitivity | Only `gpt-5.2` eligible; free models excluded; metadata `data_sensitivity_source: "org_default"` |
| E-7 | **Sensitivity 403 (D3 footgun)** — org default `restricted`, no restricted model | 403 `compliance_routing_unsatisfiable`; **no** upstream call made |
| E-8 | **Sensitivity remedy** — flip org default to `public` via the UI | All 11 models eligible again; preset matrix (E-4) still differentiates |
| E-9 | **`enable_routing: false`** — pinned model in `extra_body` | Exactly that model served; compliance/kill-switch filters still enforced |
| E-10 | **Kill-switch interaction** — isolate the deterministic winner | Reroutes to next-best deterministically; `decision_source: kill_switch` |
| E-11 | **No-LLM proof** — tcpdump/log audit during a 50-request routing run | **Zero** Bedrock `converse` calls attributed to `call_site=adjudicator`; Tier-2 scan calls still present |
| E-12 | **Bedrock outage** — set `ENABLE_TIER2=false`, then block Bedrock egress (see constraint above) | Routing still succeeds and serves the same model (routing has no LLM dependency) |
| E-20 | **Unroutable-model regression (RC-7)** — assert the reload log reports `0 invalid dropped` for the live 11-model catalog | Guards A0; a silent re-drop would return routing to remap-decided |
| E-21 | **No paid-model rebias** — request a free model with cost-dominant weights | Served model is a `:free` OpenRouter model, not `gpt-5.2`. Directly asserts the live cost defect is gone |
| E-13 | **Latency** — p50/p95 of the routing stage, before vs after | Routing stage latency drops (one fewer network round-trip); no regression elsewhere |
| E-14 | **Streaming parity** — SSE request | Same deterministic winner as non-streaming; mid-stream kill-switch still works |
| E-15 | **Multi-model real inference** — route to ≥4 distinct OpenRouter models via preset changes | Each returns a valid completion; `response.model` matches the routed model |
| E-16 | **Frontend E2E (Playwright)** — load Routing Governance, click each preset, Save, fire a request | UI state ↔ API ↔ gateway ↔ served model all agree; Routing Audit panel shows `deterministic_weighted` |
| E-17 | **Concurrency** — 50 concurrent identical requests | All 50 select the same model (no race in candidate filtering) |
| E-18 | **Malformed input** — weights as strings/negatives/NaN/`Infinity`, `data_sensitivity` as a dict, huge `latency_budget_ms` | Degrades to documented defaults; never 500 |
| E-19 | **Regression sweep** — full gateway suite + control suite + frontend suite | Green |

Evidence captured per §"Required Evidence" in `CLAUDE.md`: request/response pairs, gateway logs,
pipeline traces, Redis state, screenshots for E-16.

---

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **D3 hard floor collapses live routing to `gpt-5.2`** | **High** | E-6/E-7/E-8 prove all three states; operator remedy documented; UI warning (E4) |
| Min–max normalization changes which model existing orgs are served | Medium | Intended — the current winner is provably wrong. Before/after winner table captured for every org in Redis |
| One outlier model skews the whole min–max scale | Medium | Accepted trade-off of D2. `candidate_scores` exposes the normalized values so skew is visible |
| Deleting the adjudicator breaks ~8 test files | Medium | Enumerated in §6; each rewritten, not deleted-and-forgotten |
| Removing the sensitivity soft-fallback turns degradation into 403s | Medium | Deliberate per D3. E-7 asserts fail-closed with no upstream call |
| Historical audit rows carry `policy_adjudicator` | Low | Frontend keeps the legacy value renderable (E2) |
| `routing_catalog.py` clusters all `:free` models into one profile | Low | Out of scope; the fixed scorer still differentiates them (spread 800–1025 ms, 1.0e-05–1.9e-05) |

---

## 9. Sequencing

1. Confirm this plan; pick the D3 migration (R1 / R2).
2. Phase 0 — capture before-state: winner-per-preset for the live catalog, full test-suite baseline,
   running-image SHA vs HEAD.
3. **Phase A0 (RC-7 prerequisite)** + U-18, U-19. Confirm the reload log reads `0 invalid dropped`.
   Nothing downstream is observable until this lands.
4. Phase A (scorer) + U-1…U-7, U-10…U-14. Green before proceeding.
5. Phase B (delete adjudicator) + U-15, U-16, U-17 + update the 9 existing test files
   (incl. `test_t2_routing_overlap.py`). Green.
6. Phase C (sensitivity floor + D3 migration) + U-8, U-9, C-1. Green.
7. Phase D + E (control plane, frontend) + F-1.
8. Rebuild the gateway image from HEAD; run pre-flight P-1…P-4.
9. Full E2E §7 (E-1…E-21) against real OpenRouter.
10. Devil's-advocate review pass per `CLAUDE.md` Phase 3/4.
11. Handover report with evidence.

---

## 10. Results (2026-08-18, verified)

### Test suites

| Suite | Before | After | Verdict |
|---|---|---|---|
| Gateway | 4424 passed / 26 failed | **4505 passed / 26 failed** | +81 tests, **zero new failures** (same 26 pre-existing Tier-2 scan/masking) |
| Control | 17 pre-existing failures | **17** (+22 new tests passing) | diff **IDENTICAL** — zero new failures |
| Frontend | 125 | **128 / 128** | all green |
| **E2E live matrix** | n/a | **126 / 126 permutations** | real OpenRouter, real gateway, no mocks |

### The headline defect, before and after (live `zeroshield` catalogue)

```
BEFORE — all five presets select the SAME model, the most expensive and slowest:
  Balanced / Cost Optimized / Low Latency / Maximum Security / Quality First  ->  gpt-5.2
  dimension spreads: cost 0.004974 · latency 0.000000 · priority 0.575342 · risk 0.240000

AFTER — 8 distinct models routed across the 126-permutation matrix:
  nvidia/nemotron-3.5-content-safety:free  44   gpt-5.2                        26
  nvidia/nemotron-3-super-120b-a12b:free   22   poolside/laguna-m.1:free       15
  google/gemma-4-31b-it:free                4   cohere/north-mini-code:free     2
  openrouter/free                           1   nemotron-3-nano-omni:free       1
  cost=1.0 -> poolside/laguna-xs.2:free ($0.000010, the cheapest)
```

### "No LLM in routing" — proven, not asserted

Across the full session against the live gateway:

```
adjudicator LLM calls .......... 0        <- was the routing decision-maker
tier-2 security scans .......... 5825     <- scan path fully intact (hazard B-H1 did not fire)
routing decisions made ......... 565
model reload .................... "11 valid models from Redis (0 invalid dropped)"   <- was 3 valid / 8 dropped
ROUTING CAPACITY LOSS events .... 0
```

### Live behaviour spot-checks

| Check | Result |
|---|---|
| `cost=1.0` | routes to the cheapest model ($0.000010) |
| `priority=1.0` (SSE stream) | `gpt-5.2` (priority 95), `x-zeroshield-routing-source: deterministic_weighted` |
| Client-pinned model + routing on | `original-model: openrouter/free`, `rerouted: true` — **B-H2 honesty contract holds** |
| `enable_routing: false` | 200, model pinned exactly |
| All 6 frameworks, canonical vs drift | `hipaa`/`HIPAA`, `pci-dss`/`PCI_DSS`/`PCI DSS`, `iso-27001`/`ISO27001`, `NIST-CSF`/`NIST`, `soc 2`/`SOC2` — **all match identically** |
| Routing config compulsory on create | each of the 5 fields rejected when omitted; PATCH stays partial-friendly |

### Defect found and fixed DURING implementation

**Migration 0039 left Redis stale.** The `RunPython` used a bulk `QuerySet.update()`, which
bypasses `post_save` — so the DB read `public` while the gateway kept enforcing the old
`internal` floor and returned **403 `compliance_routing_unsatisfiable`** on a pinned request.
Caught by live E2E, not by any unit test. Fixed by pushing the corrected config to Redis inside
the migration (best-effort, so a Redis outage cannot fail the schema change), with the 120 s
reconcile retained as backstop. Regression tests added in `ConfigRedisSyncTests`.

---

## 11. Bias / fairness verification (round 2)

The concern: *"routing is properly working and not just returning the same model or giving
more weightage to any model."* Answered with distributional evidence, not spot-checks.

### Weight-simplex sweep — LIVE `zeroshield` catalogue, 1771 weight vectors

```
  43.9%  777  nvidia/nemotron-3.5-content-safety:free
  23.3%  413  poolside/laguna-m.1:free
  10.3%  182  gpt-5.2
   9.4%  167  nvidia/nemotron-3-super-120b-a12b:free
   8.0%  142  nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
   5.0%   89  google/gemma-4-31b-it:free
   0.1%    1  poolside/laguna-xs.2:free

  distinct winners : 7      max share : 43.9%      (was: 1 winner, 100%)
```

### Each dimension drives selection toward its own champion

Sweeping one weight 0 → 1 with the others held equal:

```
  cost     0.00 content-safety -> 0.50 laguna-m.1 -> 1.00 laguna-xs.2      (cheapest,  $0.000010)
  latency  0.00 content-safety -> 0.50 laguna-m.1 -> 1.00 nemotron-nano    (fastest,   400 ms)
  risk     0.00 laguna-m.1     -> 0.25 content-safety ... 1.00 content-safety (safest, 0.02)
  priority 0.00 content-safety -> 0.50 nemotron-super -> 1.00 gpt-5.2      (top prio, 95)
```

All four champions reachable and distinct — verified both offline and live through the
full gateway.

### Models that never win — checked, and correct

Four of eleven never win across 1771 vectors. Two (`gemma-4-26b`, `openrouter/free`) are
**Pareto-dominated** — never winning is right. The other two (`north-mini-code`,
`lfm-2.5`) are Pareto-optimal but sit strictly **inside the convex hull** of the
component vectors. Confirmed with **200 000 random weight vectors**: closest either ever
came to winning was a gap of **−0.0012**.

This is a mathematical property of linear scalarization — a weighted sum can only select
vertices of the convex hull — not a scorer defect. A model beaten by blends of others on
every operator priority *should* not be selected.

### Fairness test suite — `test_routing_fairness_no_bias.py`, 32 tests

Reachability · no-dominance (≤60% of simplex) · monotonicity (a champion that starts
winning must not stop as its weight rises) · responsiveness · stability under identical
models · no-reshuffle when an outlier is added · and per-framework pools for all six
frameworks still responding to weights.

The fixture asserts **no strictly dominant model exists** before testing — an earlier
draft accidentally built one, which correctly won 97% and would have masked real bias.

---

## 12. Nine-stage pipeline verification

`auth → rate_limit → policy → input_scan → kill_switch → model_routing → model_input →
model_output → output_guardrail`

`gateway/scripts/pipeline_9stage_verify.py` — **44/44 checks passed** live.

### Clean request — every stage present, ordered, timed, actioned

```
  auth             allow    0.4 ms      model_routing     allow      2.3 ms
  rate_limit       allow    0.8 ms      model_input       allow      0.2 ms
  policy           allow    5.2 ms      model_output      allow   5366.9 ms
  input_scan       allow 1261.6 ms      output_guardrail  allow    939.3 ms
  kill_switch      allow    0.3 ms
```

### Prompt injection — short-circuits correctly, no inference

```
  input_scan       BLOCK               kill_switch       skip
  model_routing    skip                model_input       skip
  model_output     skip                output_guardrail  skip
  -> error code: content_filter
```

### Compliance

| Scenario | Result |
|---|---|
| All six frameworks + `restricted` | 200, routed to `gpt-5.2` (the only model holding all six at that level) |
| Same + an unheld framework | **403 `compliance_routing_unsatisfiable`**, `decision_source: compliance_block` — fail-closed |

### Parameters verified end-to-end

`latency_budget_ms` (constraint + impossible-budget degradation) · all four
`data_sensitivity` levels · all six compliance frameworks · `enable_routing` via all four
accepted aliases · nested `weights` **and** flat `*_weight` SDK keys · `model_risk_score`
override · SSE streaming (`x-zeroshield-routing-source: deterministic_weighted`).

---

## 13. Final results

| Suite | Result |
|---|---|
| Gateway | **4537 passed / 26 failed** — same 26 pre-existing; **zero new** (baseline 4424) |
| Control | **17 pre-existing failures, diff IDENTICAL** — zero new |
| Frontend | **128 / 128** |
| E2E permutation matrix | **126 / 126**, 9 distinct models routed |
| 9-stage pipeline verify | **44 / 44** |
| Fairness / no-bias | **32 / 32** |
| Offline bias sweep | 1771 vectors · 7 winners · max 43.9% |
| Reachability proof | 200 000 random vectors |
