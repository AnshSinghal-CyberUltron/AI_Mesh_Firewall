# Requirements Document

## Introduction

This feature drives the **full nine-stage chat pipeline to ≤20 ms p50 of firewall-added latency** and
establishes the **maximum practical RPS per vCPU**, both proven end-to-end against running Docker
containers rather than component microbenchmarks.

It supersedes the latency framing in `docs/plans/2026-08-27-FINAL-evidence-based-hot-path-plan.md`,
whose central measurement (`_scan_prompt_sync` at 2,038.9 ms) **no longer describes running code**.
The `policy-driven-detection` rewrite made `_scan_prompt_sync` a passthrough (`scanner.py:1160`), and
all Tier-1 detection now flows through `policy_engine.evaluate`. Every number below was re-measured
against **this tree**.

### Measured baseline (Intel Xeon Platinum 8581C @ 2.30 GHz, Emerald Rapids, 8 physical cores)

`policy_engine.evaluate`, prompt-field rules, 2:1 regex:keyword mix, p50 over ≥12 iterations:

| rules | 512 chars | 2,048 chars | 4,096 chars (~1,024 tok) |
|---:|---:|---:|---:|
| 10 | 0.43 ms | 0.54 ms | **0.68 ms** |
| 30 | 1.22 ms | 1.54 ms | **1.89 ms** |
| 60 | 2.48 ms | 3.14 ms | **3.99 ms** |
| 120 | 5.02 ms | 6.37 ms | **7.95 ms** |
| 264 | 10.87 ms | 13.81 ms | **17.06 ms** |

Cost is **linear in enabled rule count at ~0.065 ms/rule** at the 1,024-token band. Rule count is
therefore the dominant latency variable, and it is **operator configuration, not code**.

### The mechanism, and why it is pure waste

`_search_with_budget` (`policy_engine.py:296`) routes every regex through `_run_with_timeout`
(`policy_engine.py:273`), which does:

```python
worker = threading.Thread(target=_target, daemon=True)
worker.start()
worker.join(timeout)
```

It **starts a thread and immediately joins it**. The work is serial; the thread buys *no*
parallelism. Each rule pays a full thread create + context switch + join — roughly 0.065 ms of
overhead against a regex search that is typically single-digit microseconds. This is the single
largest addressable cost on the current hot path.

### Other in-tree facts this spec must respect

| Fact | Location | Consequence |
|---|---|---|
| `async_post_llm` is **inert under `enforcement_mode="block"`** — all three branches assign `force_sync_tier2 = True` | `main.py:8364-8373` | Tier-2 Bedrock (1.3–1.8 s) cannot leave the critical path by configuration in a blocking posture |
| Gateway logger forced to DEBUG; `RedisLogPublisher` constructed unconditionally | `main.py:6019` | ~17 synchronous Redis `PUBLISH` per request; **no env var disables it** |
| `_scrub_trace_for_client` called from exactly one site — the blocked path | `main.py:957` | ~41 KiB `pipeline_trace` ships to the client on every **allowed** request; no config flag |
| `redact_all` | 8.19 ms p50 per pass over 4 KB; 4–20 passes per request | Second-largest CPU consumer after the policy engine |
| `ATTACK_PATTERNS["command_injection"]` is unreachable | `policy_engine.py:95,136`; `mcp_scan_orchestrator.py:272` read only `(prompt_injection, jailbreak)` | See `command-injection-fp-fix` staleness correction |

### Explicitly out of scope

GPU classification, cloud migration, cost modelling, and any change to what the firewall *detects*.
Detection behaviour is **frozen** by Requirement 9; this spec buys latency without buying it from
security.

---

## Glossary

- **Firewall tax** — `T_total − T_upstream`: wall-clock the gateway adds, measured from the request
  epoch, covering input path, output path and every streamed chunk.
- **Nine stages** — auth, rate_limit, policy, input_scan, kill_switch, model_routing, model_input,
  model_output, output_guardrail.
- **Stub upstream** — a token-emitting local model server. Never a non-emitting `chatcmpl-*` stub.
- **Rule budget** — the maximum number of enabled policy rules for which the latency SLO holds.
- **Posture** — `enforcement_mode` (`block` | `monitor` | `tag` | `redact`) combined with
  `tier2_execution_mode`.

---

## Requirements

### Requirement 1: The SLO is stated with a posture, a band and a percentile

**User story:** As an operator, I want a latency claim I can verify, so that I am not sold a number
measured under conditions I do not run.

#### Acceptance criteria

1. WHEN the SLO is published THEN it SHALL read: *"≤ X ms p50 and ≤ Y ms p99 of firewall tax, for
   posture P, at prompt band B, with R enabled rules, at N RPS per host."*
2. A bare "≤20 ms" without all five qualifiers SHALL fail review.
3. The primary contracted point SHALL be **`enforcement_mode=block`, ≤1,024 tokens, ≤60 enabled
   rules**, since `block` is the posture a firewall is bought for.
4. WHEN any qualifier changes THEN the harness SHALL emit a separate measured row; no row SHALL be
   interpolated.
5. Every published figure SHALL carry `[M]` (measured on the target host) or `[D]` (derived, with
   arithmetic shown). A `[D]` value SHALL NOT be presented as `[M]`.

### Requirement 2: The thread-per-regex overhead is removed

**User story:** As the gateway, I want regex evaluation not to pay a thread create-and-join per rule,
so that policy cost tracks the regex work rather than the scheduler.

#### Acceptance criteria

1. WHEN `policy_engine.evaluate` runs R rules THEN it SHALL NOT create R OS threads.
2. The ReDoS wall-clock budget that `_run_with_timeout` provides SHALL be preserved by an equivalent
   mechanism; removing the timeout is **not** an acceptable optimisation.
3. WHEN the replacement is benchmarked at 10/30/60/120/264 rules and 512/2,048/4,096 chars THEN each
   cell SHALL meet the **band-specific** floor below. A single flat multiplier SHALL NOT be used:
   the thread is a fixed ~0.0586 ms per rule, so its share falls as the regex work grows.

   | band | measured threaded → direct | required floor |
   |---|---|---:|
   | 512 chars | 15.68 → 1.30 ms @264 rules | **≥8×** |
   | 4,096 chars (~1,024 tok) | 24.93 → 10.43 ms @264 rules | **≥2×** |

   *(Measured this session; raw `Thread` create+start+join isolated at **0.0586 ms**, which accounts
   for the whole gap. An earlier draft of this requirement asserted a flat ≥3× — that is **false at
   the 4,096-char target band** and was corrected by measurement before design.)*

3a. Removing the thread is necessary but **not sufficient** at the target band: at 264 rules /
   4,096 chars the residual **10.43 ms is the regex scanning itself**. Meeting Requirement 1 at high
   rule counts therefore also requires replacing the per-pattern Python loop with a single
   multi-pattern scan (Hyperscan/Vectorscan class), which prior measurement puts at ~34 µs for 174
   patterns. Requirement 2 SHALL be delivered first because it is independent and carries no new
   dependency; the multi-pattern engine is a separate, sequenced change.
4. WHEN a pathological backtracking pattern is evaluated THEN the call SHALL still return within the
   configured budget and the rule SHALL be treated as no-match, exactly as today.
5. The verdict for every rule SHALL be **byte-identical** to the current implementation across the
   G0.1 corpus. Any difference SHALL fail the gate.

### Requirement 3: A published, enforced rule budget

**User story:** As an operator enabling policy packages, I want to know when I have bought latency I
did not intend, so that I do not silently cross the SLO.

#### Acceptance criteria

1. The system SHALL expose the count of enabled rules per org as a metric.
2. WHEN enabled rules for an org exceed the published budget THEN the control plane SHALL warn at
   configuration time, naming the measured latency cost.
3. The budget SHALL be derived from measurement on the target host, not asserted.
4. WHEN rules are enabled beyond the budget THEN the request path SHALL still function; this is a
   warning, **not** an enforcement block.

### Requirement 4: Tier-2 execution mode is never silently inert

**User story:** As an operator selecting `async_post_llm`, I want it to take effect or tell me it
cannot, so that I am not misled about where Bedrock sits.

#### Acceptance criteria

1. WHEN `tier2_execution_mode="async_post_llm"` and the posture forces synchronous Tier-2 THEN the
   system SHALL emit a structured, operator-visible signal — not only a `LOG.warning`.
2. The three-branch ladder at `main.py:8364-8373`, in which every branch assigns
   `force_sync_tier2 = True`, SHALL be replaced by an explicit single condition that states the rule
   plainly.
3. WHEN synchronous Tier-2 is forced THEN the pipeline trace SHALL record the reason.
4. This requirement SHALL NOT change *when* Tier-2 runs synchronously; blocking semantics are frozen
   by Requirement 9. It changes only the honesty of the signal.

### Requirement 5: Per-request synchronous Redis PUBLISH is removed from the request path

**User story:** As the gateway, I want log shipping off the event loop, so that a slow Redis cannot
stall request handling.

#### Acceptance criteria

1. WHEN a request is served THEN the event loop SHALL perform **zero** synchronous Redis `PUBLISH`
   calls.
2. Log records SHALL still reach the centralised channel, via a bounded non-blocking path.
3. WHEN the bounded path is full THEN records SHALL be dropped and the drop SHALL be **counted** and
   exported; silent loss SHALL fail the gate.
4. The gateway logger level SHALL be governed by `GATEWAY_LOG_LEVEL`, not hard-coded to DEBUG.
5. WHEN Redis is unreachable THEN request handling SHALL be unaffected.

### Requirement 6: The pipeline trace stops shipping on the allow path

**User story:** As a tenant, I do not want ~41 KiB of internal trace — including repeated copies of
my own prompt — returned on every successful request.

#### Acceptance criteria

1. WHEN a request is allowed THEN the response SHALL NOT carry the unscrubbed `pipeline_trace`.
2. A trace reference SHALL be returned instead, sufficient for the console to rehydrate the detail.
3. Emission SHALL be governed by an explicit configuration flag; today there is none.
4. WHEN the flag is off THEN the terminal SSE frame SHALL be **< 4 KiB**.
5. The blocked path's existing scrubbed-trace behaviour SHALL be unchanged.
6. Console "Scan Detail" SHALL remain functional, proven by a UI-level test.

### Requirement 7: A Docker end-to-end verification harness

**User story:** As a reviewer, I want the latency number produced by real containers over real HTTP,
so that no component microbenchmark can be mistaken for a system result.

#### Acceptance criteria

1. The harness SHALL bring up the full compose stack — gateway, control, Postgres, Redis, nginx and a
   **token-emitting** upstream stub.
2. It SHALL drive `POST /v1/chat/completions` with **unique prompts** over real HTTP, streaming and
   non-streaming.
3. It SHALL parse SSE and assert `|wall − addon − model_output| < ε`, the invariant established by
   `honest-stream-latency-metric`.
4. It SHALL report p50, p90, p99 and the achieved RPS, plus CPU utilisation per container.
5. WHEN any stage reports 0 ms THEN the harness SHALL distinguish *skipped* from *fast* via the
   explicit `ran` flag; a 0 ms stage SHALL NOT be silently counted as work.
6. It SHALL sweep rule count × prompt band × posture and emit a matrix, not a single number.
7. It SHALL fail loudly if the upstream stub emits no tokens.
8. Results SHALL be written to a committed evidence file with the commit SHA and host CPU model.

### Requirement 8: Maximum RPS per vCPU is measured, and stated honestly

**User story:** As a buyer, I want the throughput ceiling with its binding constraint named, so that
I can size against reality.

#### Acceptance criteria

1. The harness SHALL determine maximum sustained RPS at ≤0.1% error with guards on, and report
   RPS/vCPU.
2. It SHALL name the binding constraint at that ceiling — CPU, GIL, event loop, Redis, upstream or
   Bedrock quota.
3. The result SHALL be reported for each posture; the `block` posture is expected to be bounded by
   the Bedrock Global-profile quota (~167 RPS), not by CPU, and that SHALL be stated.
4. **A claim of 100,000 RPS per vCPU SHALL NOT be made.** Measurement to date gives ~426 RPS/vCPU as
   an optimistic ceiling for the policy engine alone on c8i-class hardware. The deliverable is the
   *measured* maximum with its constraint, not a target number.
5. Any figure quoted for hardware not under test SHALL be `[D]` with the scaling factor shown.

### Requirement 9: No detection or security-posture regression

**User story:** As a security owner, I want to know that latency was not bought with detection.

#### Acceptance criteria

1. WHEN the G0.2 posture-scoring harness is run against the G0.1 corpus before and after THEN
   recall per family SHALL NOT decrease.
2. The benign false-positive rate SHALL NOT increase.
3. Fail-closed controls SHALL remain fail-closed; no control SHALL become fail-open.
4. WHEN any verdict changes THEN it SHALL be scored, recorded and signed off as deliberate — never
   silent.
5. The full gateway test suite SHALL pass, with any pre-existing failures enumerated beforehand so
   new breakage is distinguishable.

### Requirement 10: Ordering and blast radius

**User story:** As the owner of `ansh`, I want this work isolated and sequenced so it cannot destabilise
current development.

#### Acceptance criteria

1. All work SHALL land on `dev/perf-9stage` in a separate worktree. `ansh` and `main` SHALL NOT be
   modified.
2. Requirement 7's harness SHALL be delivered **before** any optimisation is claimed; nothing is
   proven without it.
3. Each change SHALL be independently revertable and separately measured, so a regression names its
   own cause.
4. Requirement 2 (thread removal) SHALL precede rule-budget work, being the larger and more general
   lever.
5. No change SHALL be marked complete without its named test having been executed and its evidence
   recorded.
