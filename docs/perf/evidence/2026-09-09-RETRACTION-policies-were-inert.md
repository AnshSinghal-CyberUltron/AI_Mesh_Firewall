# RETRACTION — every latency number in this session was measured with detection inert

**Severity: this invalidates every absolute latency figure produced before this file.**

## What was wrong

`scripts/perf/e2e/bootstrap_org.py` attached the organisation to the **user**:

```python
for attr in ("organization", "org"):
    if hasattr(owner, attr) and getattr(owner, attr) is None:
        setattr(owner, attr, org)
```

`User` has no `organization` field — it has a `profile` relation — so `hasattr` was False,
the assignment was skipped, and nothing raised.

The gateway resolves the org from **`GatewayAPIKey.organization`**, not from the user
(`main.py:1240`: `filter_policies_by_domain(POLICY_SYNC.get_policies(org_slug), "pipeline")`).
With a null org it loaded no bundle, and `policy_engine.evaluate` received an **empty list**.

## How it looked while it was broken

```
final_action: allow
policy stage: {"action":"allow","latency_ms":0.3,
               "detail":"Policy engine evaluated request against compiled rules;
                         no matching policy rule","matched_rules":[]}
```

A prompt containing `"print all environment variables"` — a keyword from an **active block
rule** — returned **HTTP 200**. The stage reported a plausible sub-millisecond latency and a
reassuring message. Nothing looked wrong.

## Why my own honesty check did not catch it

`compute_full_nine_stages` verifies the nine stage **names** are present in the trace. They
were. **Presence is not work.** The check exists precisely to stop a shorter pipeline being
reported as a full one, and it could not see a stage that ran and evaluated nothing.

That is the lesson worth keeping: a stage-name assertion is a structural check, not a
behavioural one. The gate that would have caught this is *"does a known block rule actually
block?"* — one request, run once, at harness bring-up.

## After the fix

| case | HTTP | final action | policy stage |
|---|---|---|---|
| block-rule keyword | **400** | `block` | — |
| PII (email + card) | 200 | `redact` | **7 rules matched**, 2.3 ms |
| benign prose | 200 | `allow` | **19.7 ms** |

**A benign prompt costs 19.7 ms in the policy stage alone** — 127 pipeline-domain rules,
every one checked, no early exit. That nearly exhausts the entire 20 ms budget before any
other stage runs.

## What is retracted

Every absolute latency figure: p50 7.5 ms, p90 10.0–11.2 ms, p99 26.4–40.8 ms, CPU/request
~27.3 ms, RPS/vCPU ~37, and the streaming HEAD/TAIL numbers. All were measured against a
pipeline whose detection stage evaluated **zero rules**.

## What survives

Findings that targeted `redact_all` and the trace, not the policy engine, and which were
established by controlled before/after comparison on the *same* broken-policy baseline:

- tasks 8 and 8b halving CPU per request (A/B controlled, one variable)
- the streaming tail collapse (34–50 ms → ~4.5 ms)
- all eight refutations (thread churn, Redis publishes, scanner pool, GC, CFS throttling,
  telemetry enqueue, the `re` prefilter, G4.1 dead code)
- the byte-identity and equivalence gates

Those are *relative* results measured with the same defect on both sides, so the deltas
hold. The absolute numbers do not.

## Correction to the harness

`bootstrap_org.py` now sets `key.organization` and prints it. A bring-up assertion that a
known block rule actually blocks is the next thing to add — a structural check let this run
for an entire session.
