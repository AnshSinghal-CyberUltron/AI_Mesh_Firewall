# Design Document

## Overview

This design takes the nine-stage chat pipeline to **≤20 ms p50 of firewall tax** and establishes the
**measured** maximum RPS/vCPU, proven against running Docker containers.

It is built on measurement taken against *this tree*, recorded in
`docs/perf/evidence/2026-09-09-policy-engine-baseline.md`. The prior plan's central figure
(`_scan_prompt_sync` at 2,038.9 ms) describes code that no longer executes.

**The shape of the problem, measured:**

```
policy_engine.evaluate, 264 rules, 4,096 chars  = 17.06 ms   [M]
  ├─ thread create/start/join, 0.0586 ms x N     ≈ 10.4 ms   [M]  ← Change 1 removes this
  └─ actual regex scanning                       ≈  6.6 ms   [M]  ← Change 2 addresses this
```

Two independent levers, sequenced so each is separately measurable.

---

## Change 1 — One thread for the whole evaluation, not one per regex

### Current behaviour

`policy_engine.py:273-290`:

```python
def _run_with_timeout(fn, timeout):
    box = {}
    def _target():
        try: box["result"] = fn()
        except Exception: box["error"] = True
    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout)          # start, then immediately join — serial
    ...
```

Called once **per regex** from `_search_with_budget` (`:296`) and `:320`. At 264 rules that is 264
thread creations, measured at **0.0586 ms each**, for work that is strictly sequential.

### Design

Hoist the timeout boundary from *per-rule* to *per-evaluation*: run the whole rule loop inside **one**
worker thread, and check a monotonic deadline **between** rules.

```
evaluate(prompt, response, compiled_policies)
   └── _run_evaluation_with_deadline(rules, budget)        ← ONE thread
         for rule in rules:
             if monotonic() > deadline: mark truncated; break
             match = compiled.search(text)                 ← direct call, no thread
```

### Why this is safe, and in one respect stricter

| Property | Today (per-rule thread) | This design (per-evaluation) |
|---|---|---|
| Worst-case wall clock | N × timeout (264 × budget) | **1 × budget** — *tighter* |
| Threads per request | N | 1 |
| A pathological pattern is interruptible | No — daemon thread is abandoned, work continues in background | No — same Python limitation |
| Remaining rules after a slow pattern | Each gets a fresh budget | Skipped once the deadline passes |

The last row is the only behavioural difference. It is acceptable because
`_has_redos_shape` + `_MAX_REGEX_LEN` (`policy_engine.py:268-271`) already **reject** nested-unbounded
and oversized patterns at compile time, so the runtime budget is defence-in-depth against a rare
survivor rather than the primary control. Today's N × timeout worst case is in fact the weaker bound.

**Observability requirement:** when the deadline truncates an evaluation, the result SHALL carry a
`truncated=True` marker, the count of unevaluated rules SHALL be exported as a metric, and the
pipeline trace SHALL record it. Silent truncation of a security evaluation is not acceptable — this
is the one place this change could hide a missed detection, so it is made loud.

### Expected effect (measured floors from R2)

| band | before | after (measured direct) |
|---|---:|---:|
| 512 chars, 264 rules | 15.68 ms | **1.30 ms** (12.1×) |
| 4,096 chars, 264 rules | 24.93 ms | **10.43 ms** (2.4×) |

---

## Change 2 — One multi-pattern scan instead of a per-pattern Python loop

Change 1 leaves **10.43 ms** at 264 rules / 4,096 chars, and that residual is genuine regex work: 264
separate `re.search` calls each walking the text.

### Design

Compile all *enabled, regex-typed* rules into a **single Hyperscan/Vectorscan database** scanned once
per request, with `re` retained as the verifier.

```
build (on policy bundle change, NOT per request):
    rules → relaxed-superset transform → hs.Database(HS_FLAG_UTF8|HS_FLAG_UCP)
scan (per request):
    db.scan(text) → candidate rule ids → re.search() to confirm each candidate
```

Prior measurement (`E5`, recorded in the 08-27 plan) puts **174 patterns at ~34 µs** — roughly 300×
the Python loop.

### Two constraints that are not optional

1. **A naive ASCII port is a silent Tier-1 bypass.** Python `re` on `str` treats `\s\b\d\w` as
   Unicode; Hyperscan treats them as ASCII. A direct port produced **34 recall violations on 95
   probes** — e.g. `ignore all previous instructions` with a thin space matches in Python and is
   *missed* by Hyperscan. `HS_FLAG_UCP` alone is not the fix: **73/174 patterns fail to compile**
   under UCP because `\b` is unsupported there.
   **Required transform:** drop `\b`/`\B`, widen `\s`/`\d`/`\w` to explicit Unicode classes, compile
   `UTF8|UCP`. Measured result: 174/174 compile, **0 recall violations**, same scan cost.
2. **Hyperscan is a prefilter, never the verdict.** Every candidate SHALL be confirmed by the
   original `re` pattern. Patterns needing lookaround compile with `HS_FLAG_PREFILTER`, which is
   documented as returning a *superset* — so recall cannot drop, only false candidates appear, and
   the `re` verify removes them.

**Sequencing:** Change 2 lands only after Change 1 is merged and measured, and only after the
verdict-equivalence gate is green. It introduces a dependency (`hyperscan` wheel); Change 1 does not.

---

## Change 3 — Rule budget, surfaced

Cost is linear at ~0.065 ms/rule (0.028 ms/rule after Change 1 at the target band). The budget is
therefore derivable, not asserted:

| posture | budget @≤1,024 tok | basis |
|---|---:|---|
| today | ~60 rules | 3.99 ms measured, leaves headroom under 20 ms |
| after Change 1 | ~250 rules | 2.4× headroom |
| after Change 2 | not rule-bound | scan cost decoupled from rule count |

Exported as `policy_rules_enabled{org}` with a control-plane warning at configuration time. **Warning
only** — never an enforcement block (R3.4).

---

## Change 4 — Log shipping off the request path

`main.py:6019` sets the gateway logger to DEBUG unconditionally and constructs `RedisLogPublisher`
with no env guard, producing ~17 synchronous Redis `PUBLISH` per request.

**Design:** replace the synchronous handler with a `QueueHandler` feeding a bounded `queue.Queue`
drained by a single `QueueListener` thread that owns the Redis client. Level comes from
`GATEWAY_LOG_LEVEL`. On overflow, drop and increment `gateway_log_records_dropped_total`.

This is stdlib (`logging.handlers.QueueHandler`/`QueueListener`) — no new dependency, and the
drop-counted contract satisfies R5.3.

---

## Change 5 — Trace off the allow path

`_scrub_trace_for_client` (`main.py:725`) is called from exactly one site (`:957`, the blocked path),
so ~41 KiB of `pipeline_trace` — including repeated copies of the caller's own prompt — ships on every
**allowed** request.

**Design:** gate emission behind `GATEWAY_EMIT_PIPELINE_TRACE` (default **off**). When off, return
`{"trace_id": ...}` only; the console rehydrates detail from the audit store. Blocked-path behaviour
is unchanged.

---

## Change 6 — The Docker end-to-end harness

Nothing above may be claimed without this. It is built **first**.

```
scripts/perf/e2e/
  compose.perf.yml        gateway + control + postgres + redis + nginx + token-emitting stub
  token_stub.py           emits N tokens at a configured rate; refuses to run silently empty
  drive.py                unique prompts, SSE parsing, streaming + non-streaming
  sweep.py                rule count x prompt band x posture matrix
  report.py               p50/p90/p99, RPS, per-container CPU, commit SHA, host CPU model
```

**The invariant it asserts**, from `honest-stream-latency-metric`:

```
| wall − addon − model_output | < ε
```

Without this the harness can report the provider's TTFT as our own latency — the exact defect that
made every prior streaming number invalid.

**Refusal conditions (fail loudly, never silently pass):**
- upstream stub emitted zero tokens
- any stage reports 0 ms without an explicit `ran=false`
- reconciliation residual p50 ≠ 0
- prompts were not unique across the run

---

## Sequencing

```
Change 6 (harness)  ─── must exist first; nothing is proven without it
        │
        ├── Change 1 (one thread)      independent, no new dependency, largest safe win
        ├── Change 4 (log shipping)    independent, stdlib only
        ├── Change 5 (trace gate)      independent, config only
        │
        └── after Change 1 measured ──► Change 2 (multi-pattern engine)  new dependency
                                   └──► Change 3 (rule budget)           needs post-Change-1 slope
```

Each lands separately and is separately measured so a regression names its own cause (R10.3).

---

## Testing strategy

| Layer | What it proves |
|---|---|
| Unit | Deadline truncation marks `truncated=True`; drop counter increments; trace gate honoured |
| Property | For any rule set and any text, Change 1's verdict is **byte-identical** to the current implementation |
| Equivalence gate | G0.2 posture-scoring over the G0.1 corpus, before vs after: recall per family must not fall, benign FP rate must not rise (R9) |
| ReDoS | A pathological pattern still returns within budget and is treated as no-match |
| E2E (Docker) | The matrix from Change 6, with the reconciliation invariant asserted |
| Regression | Full gateway suite, with pre-existing failures enumerated first so new breakage is distinguishable |

---

## What this design does not do

- It does not change **what** the firewall detects. Detection is frozen by R9.
- It does not make `async_post_llm` work under `block`; that is a posture question, not a latency one.
  Change 4 of the requirements only makes the forced-sync decision *visible*.
- It does not claim 100,000 RPS/vCPU. Measurement gives ~426 RPS/vCPU as an optimistic policy-engine
  ceiling on c8i-class hardware. The deliverable is the measured maximum with its binding constraint
  named (R8.4).
