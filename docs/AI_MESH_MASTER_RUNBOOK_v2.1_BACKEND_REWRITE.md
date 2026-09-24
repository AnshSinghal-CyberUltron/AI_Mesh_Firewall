**AI Mesh Firewall**

**FINAL SCALABLE GATEWAY EXECUTION RUNBOOK**

Maximum qualified throughput under a \$5,000/month deployment envelope · p99 \<20 ms firewall overhead · local guard inference · organization-controlled security actions · OpenAI SDK + SSE compatibility · horizontally scalable backend · clean professional frontend + public landing experience

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p><strong>Execution authority</strong></p>
<p>This document is the consolidated execution runbook. It uses the September architecture/cost sources, the repository-backed V3 review, the measured Prompt Guard/TensorRT work reported in this project, and the later decisions that $5,000/month is the current deployment envelope—not a code-level ceiling. Historical 100k-RPS plans remain design evidence only; 100k is not the current release KPI under the $5k cap.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Locked dimension** | **Final position** |
|----|----|
| \$5k budget | Hard current deployment envelope. Reprice before purchase. Never hardcode this capacity in application logic. |
| Latency | p99 \<20 ms complete AI Mesh firewall-added overhead for the signed low-latency profile. |
| Throughput | Maximize qualified RPS within \$5k. Historical 1,064 RPS is a minimum planning floor to beat, not a software cap. |
| Scale | Adding CPU/GPU replicas must increase capacity without changing application semantics or rewriting the gateway. |
| Platform AI | No Bedrock, Vertex/Gemini, hosted moderation, or other platform-owned external AI dependency for enforcement. |
| Customer model | Organization-selected generation provider/BYOK remains allowed and is excluded from firewall-infrastructure cost/latency accounting. |
| User control | Organization admin controls supported content-security detectors, modes, actions and thresholds. Platform integrity controls remain non-tenant-disableable. |
| SSE / SDK | OpenAI-compatible JSON and SSE must work through official Python and Node SDKs, including tool-call streaming, cancellation and \[DONE\]. |
| Proof | No task exits on unit tests alone. Required exit evidence is live Docker/staging, real wire bytes, real browser/API behavior, and controlled-load measurements. |

**Prepared: 16 September 2026** \| Reviewed repository baseline: ansh @ e95f974dc500414b8f4db28038977fa9bf7deb44 (must be re-pinned at T00 before implementation).

> **VERSION 2.1 — validated corrections, 23 September 2026.** This edition keeps the v2 text unchanged and adds Part 0 plus inline "v2.1 CORRECTION" blocks. Where a block and the original text disagree, the block wins.

# Part 0 — Validation result and corrections (v2.1)

> **Status of this document.** v2.1 is the v2 backend-rewrite runbook with every correction that live validation on 23 September 2026 proved necessary. The original v2 text is preserved in full below; each correction appears as a **"v2.1 CORRECTION"** block directly under the section or card it changes, and the task index (§10.8) is replaced by the corrected dependency table (§0.5). Nothing here was accepted on reading alone: every correction cites an executed probe, a live GCP measurement or a line-level code/document check whose raw output is in the evidence bundle (`docs/plans/evidence/2026-09-23-runbook-v2-validation/`), and every finding went through an adversarial review whose job was to overturn it — the findings that did not survive were removed or weakened here.
>
> Validated against: repository `revamp @ 52a584e9` (HEAD) and the runbook's audited baseline `ansh @ 2a657fad`; live GCP project `ai-mesh-firewall`, asia-south1; prices from the Cloud Billing Catalog API on 2026-09-23 (on-demand basis, as instructed by the owner).

## 0.1 Verdict

**Proceed with the rebuild; do not execute the remaining v2 cards as written.**

- **The diagnosis holds.** Executed at the baseline and at HEAD: an organisation's ALLOW cannot override a scanner BLOCK (P1/P2); MONITOR still blocks on the output path (P7); the degraded-label mismatch disarms the fail-closed contract; an adapter drops flag-level findings; MCP carries its own resolver that disagrees with chat on 3 of 6 cases; streaming "block" is truncation after 94% of the text was delivered; PII in system messages and split text parts reaches the provider raw while the trace says masked. v1 measured on a 24-vCPU node, without any semantic guard: **p99 firewall overhead 1.1 s at 1 RPS** on the 70/30 SSE/JSON mix (its streaming guard holds the first ≈ 40 tokens) and **111–206 ms on JSON alone** (network floor 0.9 ms); **no rate meets 20 ms**; the highest error-free rate is 25 RPS at a p99 of 6.9–9.0 s and it collapses from 30 RPS (32.8% timeouts); 460–631 CPU-ms per request of which 69% is the proxy path itself (firewall switched off: still 327 CPU-ms per request, a 239 ms JSON p99 and a 692 ms SSE p99 at 10 RPS). A repair that only touches detection cannot reach the target.
- **The target architecture is feasible — once two things the plan does not specify are fixed.** A throwaway prototype of the §10.4 lifecycle passed end-to-end acceptance with the official OpenAI Python and Node SDKs (byte-level provider proofs for ALLOW/REDACT/BLOCK, two tenants under concurrency, truthful posture when the guard dies) at **34–40 gateway CPU-ms per request** — ≈ 15× less than v1, and less than v1's proxy path alone with its firewall switched off (327 ms; not like-for-like: the prototype omits channels v1 scans, C21) — with a JSON p99 of 14.2–14.9 ms at 25 RPS and 15.9–16.6 ms at 78 RPS. For streams, measured at the client as the worst chunk of each stream with holdback excluded (the v2.1 metric, C4), the prototype as built misses 20 ms at every load, because CPU work on the event loops delays other streams' chunks while the GPUs idle — and the gateway's own stage metrics read as a pass (C38, C39). With that work moved off the loops (no periodic metrics dump; tokenizer `encode_batch` in a thread pool) one g2-standard-24 passes to **200 RPS** (18.8–19.6 ms, 3 of 3), and a fast-CPU gateway calling a dedicated L4 guard VM over TCP passes at **191 RPS per L4** (13.9–14.3 ms, 3 of 3) for **$5.80 per qualified RPS-month** on-demand (C42). At the band's 1,024-token edge the input phase alone takes ≈ 20 ms on G2 CPUs and fails C4 even with the fix, while fast-CPU gateways with off-box guards and the fix pass it at 130 RPS (13.2 ms; 260 RPS misses at 20.06 ms). It also proved what the runbook underestimates: when its channel coverage was incomplete it reproduced v1's own defect classes (13 request fields and 4 output channels unscanned, split parts, JSON-escaped tool arguments), a priority resolver let a REDACT rule cancel a BLOCK on a different finding, and its admission let one tenant's long prompt starve another. The architecture makes these gaps *visible*; it does not prevent them.
- **The plan as written has defects that must be fixed first** (register §0.3). Before GW04: shared domain types have no legal home under the layer contract as specified; resolution across different findings is unsafe; there is no inventory of v1's ~95 hardening behaviours, so a clean-room rebuild drops them silently; shared state has no durable source of truth, so a store failover can silently disengage an engaged kill switch and un-revoke a key (both reproduced); the latency contract is structurally unmeetable for streaming output redaction, sets no per-loop blocking budget (streams stall behind other requests' CPU work) and cannot be observed from the gateway's own metrics; the capacity and cost premises (1,064 RPS, $4,561/month) were never measured and do not fit $5,000/month on any pricing basis; and GW00–GW03, although marked done, do not meet their exit criteria (no gate actually blocks a merge, the "frozen contract" suite does not run against the configured app, CI accepts failures).
- **Measured capacity replaces the plan's premises.** As specified, GW03's per-worker guard cap (≈ 1 window per worker) makes the gateway neither fast nor horizontally scalable: 1 / 2 / 4 g2-standard-24 units carry 75 / 80 / 80 RPS (efficiency 0.53 / 0.27) with the GPUs 2–12% busy, and with random (Poisson) arrivals a 3-node fleet sheds 0.46% already at 10 RPS. With the cap removed and a shared-state edge, throughput scales linearly — 150 / 300 / 600 RPS on 1 / 2 / 4 units (efficiency 1.00; shared Redis ≤ 0.04 cores, edge ≤ 1.4 of 16 cores). The largest fleet measured within $5,000/month on-demand: **3 × c4-highcpu-16 gateways + 4 × g2-standard-4 guards with the loop fix — 520 RPS (511 qualified/s; C4 17.4 / 18.2 / 17.9 ms, 3 of 3; 582 passes only 2 of 3)** for $4,161.69/month (compute + fixed platform items; $4,631.81 with the store sized for audit retention, C41); random arrivals pass at 312 RPS — against the ≥ 1,064 RPS the signed T01 contract sets as a floor. That figure excludes internet egress: at the measured 57 KB per request, sustained 24/7, egress alone would add ≈ $8,900/month, and with egress inside the envelope $5,000 buys only ≈ 172–188 sustained RPS. No hardware lets streams with output redaction meet p99 < 20 ms *including* holdback: a zero-leak redactor holds 2–3 upstream tokens per stream (40–60 ms at a 20 ms inter-token interval), which is why C4 splits the metric and asks the owner to sign a holdback bound.

## 0.2 Measured facts that replace estimates

| Quantity | v2 runbook or its cited source | Measured (conditions) | Evidence |
|---|---|---|---|
| PG2-22M latency, 1 window, L4 | ~2.14 ms (user-reported) | **2.15 ms p50 / 2.20 p99** — only with an exact-shape 1×512 TensorRT fp16 engine; dynamic profile 3.00–3.51 ms; Triton 3.98 ms | guard-bench |
| Scaling with windows W | sub-linear (4.1 / 5.9 / 10.8 / 17.2 ms, cost matrix) | **linear**: W2 4.98, W3 7.88, W4 11.01, W7 19.39 ms; batching never reduces per-window cost on the power-capped L4 | guard-bench |
| L4 ceiling | ≈490 windows/s (cost matrix) | **466 windows/s** (single owner process, batch 1) | guard-bench |
| Guard-stage capacity at p99 ≤ 10 ms | 133 RPS per L4 (derived) | **78 req/s per L4** (headline mix, 3×300 s confirmed); 44.6 req/s at W=2; 133 req/s ⇔ a guard-stage p99 of 16–20 ms with nothing else in the budget | guard-bench |
| PG2-86M | not measured | 5.94 ms W1, 12.37 W2, 19.00 W3 — cannot meet a 20 ms budget for the ≤ 1,024-token band | guard-bench |
| N sessions sharing one GPU | "in-process TensorRT" | q_safe at p99 ≤ 10 ms = **0** (vs 89 windows/s for one owner); MPS worse | guard-bench |
| CPU-only semantic guard | 159.2 ms (cost matrix) | 74–138 ms per window; infeasible for any 5–20 ms budget | guard-bench |
| Off-box guard network cost | ≥ 16.1 ms RTT (cost matrix; Mumbai→Delhi figure) | **0.084 ms p50 / 0.126 ms p99** (16 KB, same zone) | guard-bench |
| Tokenization | not in the model | ~3 µs/token on G2 (Cascade Lake) = 3.05 ms per 1,024 tokens; ~1.7 µs/token on C4 | guard-bench |
| PG2 benign false positives | 1% per window assumed | **0.21% (22M) / 0.54% (86M)** per request on the headline mix and 0.37% / 0.68% worst-band once one benign SQL snippet is excluded — **that snippet (`DELETE FROM sessions WHERE expires_at < NOW();`) scores 0.992 / 0.900 on its own** and produced almost every flag in the long-text runs: PG2 fires on benign developer SQL/code. Thresholds calibrated on short prompts fail on long text (8.0% / 100% of windows exceed the short-prompt 1% threshold). | guard-bench, reviewer-false-positives |
| PG2 recall (repo corpus, threshold 0.5) | improves on v1 | in-scope recall **34.8% (22M) / 39.6% (86M)**; prompt-injection + jailbreak + paraphrase 48.1% / 59.7%; paraphrase only 19–32%; native-script Indic injections **0/12 (22M)**, 8/12 (86M); homoglyph substitution drops a 0.999 attack to 0.0015 (22M) | guard-bench, reviewer-false-positives, reviewer-edge-cases |
| PG2 on benign system prompts | — | standard confidentiality clauses score **0.998–0.999** → 3/3 benign user turns blocked end-to-end, and the block persists across resent history | reviewer-edge-cases |
| v1 CPU per request | 0.17 RPS/vCPU (derived) | **460–631 CPU-ms/request** (JSON 87, SSE 621–664); firewall OFF: proxy path alone 327 ms (69%) | v1-bench |
| v1 capacity, g2-standard-24 | 266 RPS per node (derived) | **0 RPS within p99 < 20 ms**; highest error-free rate 25 RPS (p99 6.9–9.0 s); 30 RPS → 32.8% timeouts; overload goodput decays to ≈ 14.5–15.3 RPS; no admission control — under 5× overload the Redis pool exhausts and fail-closed checks emit spurious 422 no_provider_configured / 503 circuit_breaker_open / 503 kill_switch_active while /health stays 200 | v1-bench, reviewer-observability |
| v1 p99 T_fw_addon | < 20 ms target | **1,123–1,155 ms at 1 RPS** on the 70/30 mix (network floor 0.75–0.92 ms); JSON alone 111–206 ms at 1 RPS, 304–351 ms at 10; 6.9–9.0 s at 25 RPS; firewall OFF still 239 ms JSON / 692 ms SSE p99 at 10 RPS; v1's own audit reports overhead_ms = 0 for every stream and 4–5× too low for JSON | v1-bench, reviewer-observability |
| v1 streaming output guard | "29 guard passes" | first release waits ≈ 38–42 tokens × ITL (421 / 785 / 1,153 ms at ITL 10 / 20 / 30 ms) | v1-bench |
| 4 × BaseHTTPMiddleware | 1.00 ms p50 / 4.70 ms p99 | +0.36–0.47 ms p50, +0.64–0.74 ms p99 at low load (to +6 ms near saturation), +0.45–0.58 ms CPU; **16× lower throughput per worker**; pure ASGI costs nothing measurable | micro-claims |
| Synchronous Redis log publish | "every log line" | 2–14 publishes per request; 0.14–1.0 ms normally, **66 ms at 5 ms Redis latency** | micro-claims |
| nginx without upstream keepalive | churn ceiling | **HTTP 502 above ~420 req/s** per edge→gateway pair; with keepalive ≥ 30,000 req/s at p99 < 0.4 ms | micro-claims |
| Cost of the cost-matrix fleet | $4,561.44/month (3-yr CUD) | **$8,952.24 on-demand** (owner's basis); like-for-like on the source's own 3-yr CUD basis with Mumbai corrections $5,238.01; G2 1-yr CUD $6,702.01 — **over $5,000 on every basis** | pricing-verifier, reviewer-false-positives |
| G2/L4 availability | assumed | **ZONE_RESOURCE_POOL_EXHAUSTED / STOCKOUT** for g2 in asia-south1-a/b/c at 07:18–07:20 UTC, 11:40–11:54 UTC (also asia-southeast1, asia-east1), asia-south1-a/c at 18:40–18:42 UTC and asia-south1-b at 20:41–20:43 UTC — 36 of 170 VM creations failed (Cloud Audit Logs) | guard-bench, proto-builder, reviewer-edge-cases, controller |
| v2 prototype gateway cost | — | **35–39 CPU-ms per request** (33.5 ms marginal + 0.14 cores fixed, of which ≈ 0.12 cores is the bench's per-second metrics dump) at 25–78 RPS; JSON p99 14.2–14.9 ms at 25 RPS and 15.9–16.6 ms at 78 RPS (5-min steps, 3 repeats at 78, errors counted as +∞) | proto-bench-unit, reviewer-observability |
| v2 prototype streaming (strict rule) | p99 < 20 ms incl. holdback | **fails at every rate**: the minimal pattern-aware holdback holds each word for 1–2 inter-token intervals ('.' is in the word class) — per chunk p50 20 / p99 40 ms, **per stream p50 40 / p99 60 ms** at ITL 20 ms; the first provider token reaches the client 29 ms later at p50 (45–50 ms p99); unbounded for URL/UUID/base64 runs (300 ms – whole stream) | proto-bench-unit, reviewer-hidden-failures, reviewer-observability |
| v2 prototype, one g2-standard-24, knee under GW03's per-worker bound | 1,064 RPS / 4 nodes ⇒ 266 per node | **78 RPS** with evenly spaced arrivals, judged by end-to-end duration (GPU 11–12% busy, 2.75 of 24 cores) — bound by the per-worker guard admission cap, which behaves as a loss queue: Poisson arrivals shed 3.5% at 78 RPS; < 0.1% would need ≈ 2.2–2.6 RPS per node | proto-bench-unit, reviewer-observability |
| v2 prototype, one g2-standard-24, under the v2.1 streaming metric (C4: per-stream worst chunk, holdback excluded) | — | passes ≤ 50 RPS (18.1–19.2 ms p99 with the bench's per-second metrics dump removed; 19.0–20.4 with it), borderline at 63 (20.3), **fails at 78 (20.4–21.3) and 100**; 1,024-token band fails at 25 RPS (21.3); cause: worker event-loop blocking (loop lag p99.9 10.4 ms) with the GPU ≤ 15% busy; the gateway's own stage metrics read PASS (under-report ≈ 8 ms at p99) | reviewer-observability |
| v2 prototype, one g2-standard-24, hardware knee (corrected metrics) | — | **200 RPS** by C4 once CPU work is off the event loops (18.82–19.61 ms, 3 of 3; 250 fails at 23.96); as built no rate passes C4 (floor ≈ 20.0–20.5 ms even at 8–40 RPS); load rule without the per-worker cap 150 RPS (175 fails); binding: input-phase latency (tokenization on Cascade Lake + serial batch-1 guard queue) and intermittent ≈ 40 ms single-worker stalls from 200 RPS; GPU ≤ 30% | proto-bench-unit, proto-bench-fleet |
| Scaling 1 → 2 → 4 units | ≥ 1.7× at 2 units | as specified (GW03 per-worker cap): **75 / 80 / 80 RPS** (efficiency 0.53 / 0.27); cap removed + shared round-robin edge: **150 / 300 / 600 RPS** (1.00 / 1.00, load rule; shared Redis ≤ 0.04 cores, edge ≤ 1.4 of 16 cores); split topology by C4: 1 gateway + 1 guard 191, 1 gateway + 2 guards 298 (gateway loops bind), 2 gateways + 2 guards 298 | proto-bench-fleet, proto-builder |
| Off-box guard: c4-highcpu-16 gateway + g2-standard-4 guard over TCP | remote_http = "off-box burst"; ≥ 16.1 ms (cost matrix) | **191 RPS per L4** by C4 (13.85 / 14.24 / 14.32 ms, 3 of 3; 238 fails); random arrivals < 122; 10.5 gateway + 3.8 guard CPU-ms per request; TCP hop ≈ 0.6 ms per guard call — the same as the local Unix socket; $1,089.14/month per pair ⇒ **$5.80 per qualified RPS-month** | proto-builder, controller C4 recompute |
| Egress per sustained qualified RPS | — | ≈ 57 KB to the client + 9–10 KB to the provider per request (SSE framing ≈ 300 B per output token) ⇒ **≈ $17.4 per RPS-month** on Premium tier — more than the compute per RPS | proto-bench-fleet, reviewer-observability |
| Random (Poisson) arrivals vs evenly spaced | — | one gateway + one L4 guard: **98 RPS** (C4 19.00 / 18.88 / 19.52 ms, 3 of 3) vs 191 with constant arrivals; guard owners in a multi-guard fleet see near-random arrivals, so per-guard capacity halves unless calls are balanced fleet-wide (C43) | proto-builder (split), controller C4 recompute |
| 1,024-token band edge (worst band) | inside the 20 ms SLO | all-in-one G2 with the loop fix: **fails at every rate** (21.65 ms at 25 RPS); off-box fleet with the fix: **130 RPS passes** (13.22 ms), 260 misses (20.06) | proto-bench-unit, proto-builder |
| Max qualified RPS within $5,000/month on-demand | 1,064 | **520 RPS (511 qualified/s)** by C4, 3 of 3 (17.37 / 18.20 / 17.89 ms): 3 × c4-highcpu-16 gateways + 4 × g2-standard-4 guards + edge + shared store, loop fix on, $4,161.69/month (compute + $329.50 fixed; $4,631.81 with the audit store sized, C41); 582 passes 2 of 3; random arrivals 312 RPS pass (15.87 ms); same fleet shape without the fix (3 + 5 guards, $4,726.46) 466 RPS. Egress excluded (≈ $17.4 per sustained RPS-month; with it inside the envelope ≈ 172–188 RPS) | proto-builder (split), controller C4 recompute |

## 0.3 Corrections register

**Impact**: CRITICAL = a policy the tenant selected can be silently defeated, or the release claim cannot be true; HIGH = a card cannot meet its own exit criteria or the objective, as written; MEDIUM = wrong or ambiguous plan text with a bounded consequence; LOW = documentation. **Fix before** = the latest point at which the correction must be applied.

| ID | Where | What validation proved (evidence) | Correction | Impact | Fix before |
|---|---|---|---|---|---|
| C1 | §10.3.2, §10.3.3, §10.5, GW04 | The interfaces as specified put Finding in detect/base.py, Category/ExecutionPlan in plan/model.py and Decision in resolve/decision.py, and have egress call detect and resolve: 12 illegal layer pairs over 17 imports under the declared order (executed). The order itself is satisfiable: with the shared value types in the lowest layer and detect/resolve injected into egress, lint-imports is KEPT with the identical layer list and mypy --strict is clean (proven twice, independently). | Put every shared value type and protocol in the lowest layer (`contracts/` or a new bottom `domain/`); `edge/` injects the detect/resolve callables egress and dispatch need; add a contract that `resolve/` imports only that layer. | HIGH | GW04 |
| C2 | §10.8, card "Depends on", §10.12, §10.13 | The index, the sequencing prose ("everything else is a strict chain") and the §10.13 lanes disagree; 28 acceptance tests need artifacts from cards that run later; GW16–GW18 rejoin at GW20, not GW19; an agent that follows "Depends on" literally starts GW15 without admission or detectors. | Make §0.5 the single authoritative table and relocate the listed tests. | MEDIUM | GW04 |
| C3 | §10.2.1, §10.12, GW05, GW14, UI06, UI12 | A second customer-visible contract exists. UI06 (versioned adapter) and UI12 (reconcile against v2) cover the browser end, but no card owns the control-plane middle tier: telemetry drain → EnforcementEvent (12 console endpoints), pipeline_trace (137 keys, 87 read by the UI), the `zeroshield` object and 10 X-ZeroShield-* headers (invisible to the SDK and to the WIRE bucket), control↔gateway admin HTTP, and 65 console-written config fields; UI12 would only detect the breakage on GW23's critical path. | New card **GW14b**; CONSOLE bucket in the parity differ. | HIGH | GW14 |
| C4 | §1.2, T01, GW12–GW14 | Boundary-safe streaming redaction must hold back any suffix that could still become a match until the next upstream token arrives; that wait is bounded below by the provider's inter-token latency. v1: first release waits ≈ 38–42 tokens × ITL. Prototype: 1–2 ITLs per word ('.' is in the word class) — per chunk p50 20 / p99 40 ms, per stream p50 40 / p99 60 ms at ITL 20 ms; the first provider token reaches the client 29 ms later at p50 — unbounded for URL/UUID/base64 runs. p99 < 20 ms "including all hold" (T01) is structurally unmeetable for streaming with output redaction. | Split T_release_lag into T_release_processing (in the SLO) and T_holdback_wait (published separately with a signed bound per mode); re-sign T01. | HIGH | T01 re-sign, GW12 |
| C5 | §1.3, §1 table, §6, T01, T20 | 1,064 RPS is derived from vendor figures (top of its source's 590–1,064 range) and was never measured; $4,561.44 is the 3-year CUD price; the fleet costs $8,952.24/month on-demand and is over $5,000 on every pricing basis checked; the ≥ 300 RPS/vCPU target has no source. | Replace with measured numbers (§0.2) and an explicit pricing basis; re-sign T01 with them. | HIGH | T01 re-sign |
| C6 | T04, GW10, §10.2.2 | PG2 is a narrow instruction-override detector: in-scope recall 35–40% at 0.5, paraphrase 19–32%, native-script Indic injections 0/12 (22M) / 8/12 (86M) — T01 signs an English-only headline and GW10 already requires multilingual results to be published separately, so this quantifies a named risk for a Mumbai deployment rather than contradicting the plan — homoglyphs defeat it, RAG indirect injection 1/4; benign developer SQL scores 0.99, benign confidentiality system prompts 0.998 (3/3 benign turns blocked, sticky across history); thresholds calibrated on short prompts fail on long text; the shipped tokenizer.json truncates at 512. The T04 corpus is 748 single-window ASCII items. | T04 adds long/multi-window, multilingual, obfuscation, developer-code, system-prompt and indirect-injection families with span/action labels; GW10 calibrates per window count, disables tokenizer truncation, adds language identification with an explicit posture for unsupported languages, and scopes semantic rules by message role; default semantic action FLAG until a signed per-request FPR and recall floor are met. | HIGH | GW10, T04 |
| C7 | §10.5.4, GW08, GW10, §10.6 | On L4 one process must own each GPU (N sessions → 0 capacity at p99 ≤ 10 ms; MPS worse); micro-batching is worse at every setting; per-worker bounds do not bound a shared queue (overload → UNAVAILABLE → FAIL_OPEN tenants unscanned); TensorRT session creation holds the GIL 18–22 s; ORT silently falls back to CUDA without `disable_fallback()`. | One owner process per GPU (exact-shape 1×512 fp16 engine, batch 1, FIFO), reached over Unix socket or TCP (~0.1 ms off-box); admission at the owner; TensorRT asserted first at readiness. | HIGH | GW08 |
| C8 | §10.7.1–10.7.3, GW02, GW21 | Production has no traffic, so C2 cannot be a capture; the implemented C2 is 40 distinct requests × 1,250, replay never calls v1, and the "v1 oracle" is a regex live v1 no longer runs. The git-timestamp ledger check cannot prove pre-registration. The T02 recorder emits one chunk. Shadow ≥ 72 h / ≥ 5 M live requests needs 19.3 RPS of traffic that does not exist. | Declared, hashed synthetic C2 replayed through the real v1 and v2 and a streaming recorder; pre-registration by commit ancestry; shadow = long synthetic replay + real-provider tee once tenants exist. | HIGH | GW02 re-exit |
| C9 | §10.7.4, GW21–GW23, T24 | With no production traffic the big-bang rationale is moot; once tenants exist, per-tenant cohort cutover avoids the stated objection completely (0/200 tenants inconsistent vs 43–158/200 under per-request splitting). Shadow against a synthetic recorder cannot validate the output path or streaming modes. | Cut over by tenant cohort; T24 (real-provider canary on v2) runs before GW23 as cohort 1. | MEDIUM | GW21 |
| C10 | GW00–GW03 (marked done) | GW00: pinned baseline 2a657fad cannot serve chat (HTTP 500); the installed pre-commit hook is the pre-GW00 copy; the secret gate misses PKCS#8 "BEGIN PRIVATE KEY", suffixed files, dotfiles and history; **no gate blocks anything** — revamp is unprotected, required checks are off, all 11 GW runs were direct pushes, commit 4e74c573 landed red, and the image digest is uploaded but never compared; the import-linter gate can be silently disabled. GW01: the suite imports v1 directly (27 tests pass against a closed port); CI accepts pytest exit 1; 2 cases fail at HEAD. GW02: see C8. GW03: hidden assumptions (per-worker RSS 400 MiB → OOM with an in-process 86M worker at 6.77 GB), queue_depth independent of the p99 target, the literal gate catches 0/14 hidden forms, the cgroup code ignores parent-cgroup limits (derived 12 workers where the truth is 1), SIGHUP reload deadlocks 3/3 (a non-reentrant lock taken in the signal handler) and recurses to a crash under rapid signals — LGW03-5's test cannot see it —, refuse-to-start at 1–1.3 vCPU, and the per-worker guard share it derives (≈1 window) is what caps the prototype's measured knee — as a loss queue: with Poisson arrivals it sheds 3.5% at 78 RPS (1.6–1.9× the Erlang-B prediction), and < 0.1% sheds would need ≤ 0.12–0.14 req/s per worker (≈ 2.2–2.6 RPS per g2-standard-24). One target knob couples the per-worker guard cap, the input-queue cap and the owner cap, so removing the per-worker cap also disabled owner shedding; the guard deadline equals the 20 ms SLO target, so any guard hiccup becomes a fail-closed BLOCK. | Re-open each card on these items (card blocks below); protect `revamp` with required checks. | HIGH | now |
| C11 | §10.4, GW07, GW11, §10.2.3 | "BLOCK cannot reach a provider, by type" is not enforceable in Python: with a sealed constructor, direct construction and `dataclasses.replace` are stopped, but `object.__new__`+setattr and relabel-the-Decision-then-mint still reach the provider. The HTTP gate misses positional/named status codes, 5xx and raised exceptions, and is evaded by import aliases. | Sealed constructor + provenance AST gate (caught 17/17 forgery forms) + runtime assertion + recorder zero-call proof; import-linter forbidden contract for starlette/fastapi outside `edge/`. | MEDIUM | GW07 |
| C12 | §10.2.3, §10.3.1, §10.4, GW06, GW19 | admit/ (401/429/503, kill-switch), plan/ (PLAN_UNAVAILABLE) and dispatch/ (verification failure) produce terminal outcomes outside resolve/. | Scope "single authority" to content decisions; one posture table for admission facts, implemented once; only `edge/` renders responses. | MEDIUM | GW06 |
| C13 | GW18, GW24, T25 | T08/T25 already route control-plane security engines to GW24, but GW24's delete list names gateway modules only; default-on external AI also exists in output Tier-2, the Bedrock credential preflight at every boot and the control-plane warm-up and `/api/security/scan/`. BYOK embedder fallback, the worker's default embedding and keyless bedrock rows are credential-revocation hazards (retrieval/generation), not §1 violations. | Itemize GW24's removal list and extend LGW24-1/-2 to control plane, workers, start-up and background jobs; correct GW18's wording. | MEDIUM | GW24 |
| C14 | §10.1 | Numbers corrected by execution (block under §10.1); the rebuild conclusion stands on the corrected evidence and gains new defects found during validation — including, under load: stream audit records attributed to the wrong request (a context variable read at emit time; strict completeness 0.78 / 0.59 / 0.51 / 0.32 at 30 / 40 / 50 / 100 RPS), self-reported overhead_ms clamped to 0 for every stream and 4–5× too low for JSON, /health returning 200 while clients fail, and disabled stages audited as executed "allow". | Replace the §10.1 figures. | LOW | — |
| C15 | GW14, GW20, §10.11 | Residual rule contradictory ("p50 zero" vs "void if non-zero at any point"); /proc/stat under-reports bursty CPU on these kernels. | One residual rule; per-task utime+stime or /proc/schedstat. | MEDIUM | GW14 |
| C16 | T01, T04, §10.12 | T01 was signed by an AI agent, not the named owners, and omits the $5k envelope, failure postures, freshness bounds and overload policy; T04 never ran; dropped T acceptance tests (incl. L07-2 encodings, T03 cancellation accounting, T17 per-tenant queues) would be skipped because agents exit on their own card's tests. | Re-sign T01 (with C4, C5, C32); run T04 (C6); add the dropped tests to the absorbing cards. | HIGH | GW04 |
| C17 | §9 | 13 stale T-references; T22↔UI12 cycle; UI13 not ordered after GW23; production bundle 648,563 B gzip in one chunk (1.85× shell, 3.24× landing budget; ECharts 69% of the shell budget); /login hard-codes "Uptime 99.99%", "SOC2 / ISO", "10M+ calls"; the console runs as a Vite dev server. | Re-map (§9.5 block); route-level splitting before UI10; remove unqualified claims now; bake the console. | MEDIUM | UI02 |
| C18 | §1.3, §7, T20, GW20 | G2/L4 capacity was exhausted in every asia-south1 zone twice on 2026-09-23 and in two neighbouring regions. | Reserve capacity (priced inside the envelope) or qualify a multi-zone/multi-region guard pool and a degraded-capacity posture. | HIGH | T20 |
| C19 | §1 table, §10, §10.13 | Three different baseline pins; human authority exists at more than the "four" points. | One pin (revamp HEAD at GW04 start, correlated to the deployed image); list every human gate. | LOW | GW04 |
| C20 | §10.1.6, T16 | Without upstream keepalive nginx returns HTTP 502 above ~420 req/s per pair; with it ≥ 30,000 req/s. | New card **GW16b** (production edge). | MEDIUM | GW20 |
| C21 | §10.2.1–10.2.2, §10.5.1, GW04, GW09, GW13, GW15 | "Hold equivalence only where measured good" has no inventory of v1's ~95 hardening behaviours (input-channel folding, output-channel scanning, encodings), and neither C1, C2 nor the one-chunk recorder can exercise them. The prototype dropped them and leaked: 13 request fields forwarded unscanned under BLOCK with det:E claimed; logprobs=true defeats output REDACT while audit says REDACT; refusal/reasoning/function_call outputs never inspected; secrets split across parts or JSON-escaped in tool arguments pass; redaction breaks json_object validity; output is never canonicalized. | Before GW04: a machine-readable **hardening inventory** of v1's behaviours, each mapped to a v2 test; the domain types enumerate every request and response field that carries text (from the pinned SDK types), with a CI gate that fails when an SDK text field has no scanner mapping; scan joined parts and decoded tool-argument values with span maps; validate structured outputs after redaction. | CRITICAL | GW04 |
| C22 | §10.5.3, GW05, GW07 | Priority resolution across *different* findings lets a higher-priority REDACT or FLAG rule cancel a BLOCK on another finding (the AWS key reached the provider); at equal priority, renaming a rule flips the outcome. §10.5.3 permits this and GW05's compiler does not reject it. | Resolve each finding under its own rule; the request disposition is the most restrictive per-finding disposition (a BLOCK on any enforced finding survives); priority orders only rules that match the same finding; tie-breaks never depend on names; property test: adding a REDACT rule never removes a BLOCK. | CRITICAL | GW07 (spec by GW04) |
| C23 | GW08, GW19, T17 | Admission is tenant-blind: one tenant's legitimate 16-window prompt held the GPU for 34–44 ms and another tenant got 30/30 × 503, with owner shedding too; shed responses make the SDK retry 3×. | Per-tenant weighted fair queuing at every shared resource; sheds carry `x-should-retry: false` or an adequate Retry-After; test tenant B's share and p99 while A saturates. | HIGH | GW19 |
| C24 | GW06, GW12, GW13 | In-flight streams ignore the kill switch, key revocation and plan changes (~93% of the content delivered after the flip); there is no maximum stream duration. | Sign in-flight semantics per control; test on a 5-minute stream. | MEDIUM | GW12 |
| C25 | GW12, GW13 | Holdback rescans the held run on every chunk (O(n²)): 32 KiB of base64 cost 17.9 s CPU and stalled a bystander 12 s; 64 KiB held the whole stream; stream_buffer_bytes (47–76 GB) never binds; plans with MONITOR-only output rules still hold back. | Bytes scanned per byte released must be O(1); a bounded held-byte ceiling with a declared outcome; hold only when an enforcing output rule exists and only for its selected pattern classes. | HIGH | GW12 |
| C26 | GW06, GW09 | Canonicalization and tokenization run on the event loop before size rejection: an 18× NFKC-expanding 1 MiB body cost 7.4 s CPU and stalled a bystander 6.8 s; unauthenticated requests pay the full parse. | Cheap pre-checks (size, expansion ratio, part counts) before authentication-independent heavy work; bounded canonicalization off the serving loop. | HIGH | GW09 |
| C27 | GW11, GW12, GW15 | A fixed 120 s provider timeout kills legitimate long generations (base-URL-swap contract broken; SDK retries → 3× generation cost); provider 400/429 surface as 502 without Retry-After; unbounded non-stream bodies grow RSS 3× (one BYOK tenant can OOM a shared worker); provider error frames echoing a secret are forwarded unscanned; over-band multi-turn conversations get 413 from turn 13 instead of `context_length_exceeded`; cancellation leaves requests unaudited. | Plan-declared timeouts per provider/model; exact error-class mapping with headers; response-size bound; scan provider error payloads; incremental history scanning and an SDK-compatible over-band error; audit every started request. | HIGH | GW11 |
| C28 | GW05, GW14 | Concurrent reconciles revert plans while the version index says current: replicas stay on stale or missing plans permanently (75–83 tenants stale at +60 s on a live 4-worker server; 413 tenants never loaded) while plan age < 1 s and /readyz is ready; reconcile is O(orgs) on every worker's event loop (100k orgs: 55 ms stall per second); per-org metrics give 50,003 series per worker at 50k tenants. | One serialized reconcile task per process fed by pushes; compare against the served plan's own version; delta reconcile off the serving loop; convergence proven by served-version sampling, not snapshot age; metric cardinality independent of tenant count. | HIGH | GW05 |
| C29 | GW06 | Quota leases strand budget in worker-local leases (false 429s with up to 92% of the budget unused; leases never returned and lost on every restart); a policy-BLOCKED request with zero provider calls was charged 4,032 tokens and pulled a 51,360-token chunk; the chunk is derived from the resource contract, not the org's budget; per-org GCRA multiplies by process count (28 admitted where 8 were allowed). Reproduced at fleet scale (4 units × 18 workers, 56 RPS): with the contract-derived chunk of 205,440 tokens, 20 of 72 workers leased the whole 4,000,000-token budget within seconds, 84.1% stayed stranded and 92.6% of requests got 429 insufficient_quota while budget remained; a 4,096-token chunk admitted 99.3% (0.7% stranded, 0.23 store round trips per admitted request); overshoot was 0 in both. | Lease records with a TTL on the store's clock, renewal and return on shutdown or idle; chunk ≤ remaining budget / active workers; refund when nothing is dispatched; true-up against actual usage; shared per-org rate state. | HIGH | GW06 |
| C30 | GW08, GW19 | A stuck inference thread keeps readiness green while FAIL_OPEN tenants run unscanned; one worker crash takes the whole unit down (no respawn); a client that stops reading blocks drain forever. | Readiness from recent successful guard replies; per-worker crash isolation and respawn; write/idle timeouts and a bounded drain. | HIGH | GW19 |
| C31 | GW14, GW20, §10.7, the measurement harness | The "load knee" metric is blind to mid-stream delay; "first content" is a lone space in ~98% of streams; per-chunk lag was sampled on 10% of streams; errors were dropped from the p99 instead of counted; the benchmark's output shape (short English words, ASCII input, 3 orgs) never exercises the expensive paths; instrumentation ran on the serving loop (a metrics dump stalled the guard owner 200–380 ms every 10 minutes). | 100% per-chunk sampling in capacity runs; "first" = the event that completes provider token 1; p99 over all offered with errors as +∞; realistic output shapes (URLs, UUIDs, base64, digits, code), non-ASCII input and ≥ 10k tenants in the workload; no synchronous I/O on serving loops; non-exact canary matching. | HIGH | GW20 |
| C32 | T01, T23, T24 | With no production traffic every capacity and quality claim rests on assumed traffic shape (multi-turn share, Indic share, system prompts, output shapes, tenant skew, long generations, tenant count, retry behaviour). | A signed traffic-shape assumption register in T01, one synthetic stratum per assumption in T23, and a T24 canary that fails the claim if real traffic falls outside it. | HIGH | T01 re-sign |
| C33 | GW17 | GW17's tests omit MCP resources/read, prompts/get arguments, notifications/progress interleaved in SSE, structuredContent, sampling/createMessage and elicitation; v1 scans most of these. | Add them to GW17's scope and tests. | HIGH | GW17 |
| C34 | GW16 | Embeddings: a BLOCK in one item of a batch, per-item decisions vs "one DecisionRecord per phase", and token-array inputs are undefined. | Define and test them. | MEDIUM | GW16 |
| C35 | GW08 | The guard owner's model-identity check compared an environment value with itself: swapping the 22M bytes for 86M under the pinned hash was accepted and injection detection silently changed (403 → 200, both reported as executed). A fleet-wide image or driver rollout rebuilds every engine at once (GIL held 18–22 s each); engine caches omit the driver version; independently built fp16 engines can differ (max Δp 0.0167). | The owner hashes the model, engine and tokenizer bytes it actually loaded and workers compare them against a signed manifest (mismatch = UNAVAILABLE); cache key includes driver and TensorRT versions; staged rollout; one engine artifact per version. | HIGH | GW08 |
| C36 | GW05, GW06, §10.5.2 | "Store flaps → last-known-good" covers unavailability, not DATA LOSS. In the prototype: after a store flush or an empty-replica failover, re-publishing the same versions wedged tenants at 403 "complete onboarding" forever while valid keys got 401 and /readyz said 200; a failover with ordinary replication lag silently disengaged an engaged kill switch (missing key = off) and un-revoked a key (epoch 2→1 accepted); a reused version string made the output phase enforce stale content and deliver raw PII while headers and audit showed one version; version regress was accepted; last-known-good lived only in memory; a cache-fill race re-admitted a revoked key; background store calls used a 5 s timeout equal to the 5 s staleness ceiling (fleet-wide fail-closed on every half-open failover); dead pooled connections produced 75/150 spurious 503s after a clean failover. v1 had fixes for the flush and resync classes that v2 regresses. | A durable source of truth (Postgres) with control-plane re-hydration of plans, keys, kill switches and the auth epoch; versions = (epoch, sequence) + content hash, strictly monotonic, regress only by signed rollback; the kill switch stored with an explicit OFF record — absence or regress means UNAVAILABLE (fail closed); missing plan data is PLAN_UNAVAILABLE (503), never unknown tenant; durable last-known-good; epoch-checked cache fills; store-op timeouts shorter than the refresh period, validated at start-up; retry idempotent reads once on connection errors. | CRITICAL | GW05 |
| C37 | GW21, GW22, §10.7.3 | v1 and v2 on one store have no key collisions but diverge in meaning: a console kill switch stops v1 but not v2; a console revocation (DEL) is ignored by v2 (216/216 accepted over 20 s); a v2 kill switch is ignored by v1 after rollback; pub/sub is not scoped by db; two quota ledgers. Under the repository's production store policy (maxmemory 768 MB, allkeys-lru) v2's audit records (3.3 KB each) evicted **every security key of both versions** — including an engaged kill switch and actively used API keys — after 13,600 records in a scaled test. | One control-plane writer emitting both formats during coexistence; one quota ledger; audit on a separate store; security state in a non-evicting store (noeviction or a dedicated instance); include all of it in the GW22 rehearsal. | HIGH | GW21 |
| C38 | §2.1, §10.4, §10.5.5, GW12, GW20 | Streaming egress shares each worker's event loop with CPU-bound input work (tokenization ≈ 3 ms per 1,024 tokens on G2's CPUs, scanning) and periodic work (metrics exposition 5.2 ms per scrape). Measured at the client as the worst chunk of each stream with holdback excluded, the prototype passes at ≤ 50 RPS but exceeds 20 ms at p99 from 63–78 RPS per g2-standard-24, and at every rate for 1,024-token prompts, with the GPU ≤ 15% and ≤ 3 of 24 cores busy; gateway loop lag p99.9 10.4 ms ≈ the client-side excess (10.0 ms). The runbook sets no per-loop blocking budget and no metric that would expose it. | Loops that forward streams never run CPU-bound work longer than a declared slice: tokenization and scanning run in executors that release the GIL or in separate processes; metrics exposition runs off the serving loop; loop lag is an SLO input; acceptance test: inject a CPU burst on a worker mid-stream and require the other streams' worst-chunk p99 to stay in budget. Measured fix on the same G2 unit at 78 RPS: no periodic metrics dump on the loop + tokenizer `encode_batch` in a thread pool (`encode` does not release the GIL; `encode_batch` does — 2-thread speedup 1.01 vs 2.01) took C4 p99 from 21.5 to 15.0 ms, loop lag p99 from 7.2 to 1.0 ms. | HIGH | §2.1 now, GW12 |
| C39 | §1.2, GW14, GW20 | The SLO cannot be observed from gateway stage histograms: there is no per-request T_fw_addon (a p99 of a per-request maximum cannot be derived from component histograms); T_input starts at ASGI entry, after parsing and scheduling (the client sees +1.0–1.1 ms at p50 and +2.1–2.5 ms at p99 at 25–100 RPS, up to +7.9 ms on long streams); release processing starts after the loop has read the chunk (gateway p99 0.107 ms vs a client per-stream excess of 10.0 ms). At 78 RPS every gateway proxy read PASS while clients saw 21.9 ms. LGW14-1..3 inject delay inside the gateway's stopwatch, so they cannot catch this class. | Socket-level arrival timestamps for the request and every upstream chunk; a per-request T_fw_addon histogram (maximum over chunks) with loop-lag attribution; an acceptance test that blocks the event loop mid-stream; a black-box synthetic client + provider canary in production (production has no client clock) whose p99 is the published SLO. | HIGH | GW14 |
| C40 | GW14, GW19 | Admission sheds (503) leave no audit record while audit_completeness_ratio reads 1.0 (unaudited sheds 0.075–5.75% of admitted requests in the unit runs, up to 12.5% in fleet runs); readiness has no load input, so sheds are visible only as a counter; LGW14-7 (join ≥ 10,000 audit records against provider and client bytes) was never executed, so per-record truthfulness is unproven — only the counters reconcile. | Every admitted request — including sheds, cancellations and fail-closed outcomes — produces a record; completeness is measured against admitted requests; per-tenant shed reasons are exported; LGW14-7 runs in GW20. | MEDIUM | GW14 |
| C41 | GW14, GW06, §1.3 | The audit sink's retention lives in the hot-path store: 2.89 KB per record in Redis with MAXLEN 2,000,000 per org ≈ 5.8 GB per org. The cost envelope's 1 GiB Memorystore fills in ≈ 41 minutes at 75 RPS; under noeviction the audit XADD then fails (and, by code, lease refills fail → 503 for everyone), and under the repository's allkeys-lru policy audit records evict security keys instead (C37). | Size the audit sink from retention × rate × measured record size and keep bulk retention out of the admission/quota store (stream to a durable sink; the store holds a bounded buffer with backpressure accounting); a store-memory alarm below the eviction point; price the sized store in the envelope (a 13 GiB tier adds $470.12/month). | HIGH | GW14 |
| C42 | §1.3, §10.5.4, GW08, GW20 | The topology §10.5.4 lists only as "remote_http — off-box burst" is the most cost-effective one measured: fast-CPU gateways (c4-highcpu-16, 10.5 gateway CPU-ms per request vs 30–34 on G2's Cascade Lake) calling one guard owner per L4 on g2-standard-4 VMs over TCP (≈ 0.6 ms per call — no more than the all-in-one's local Unix socket) pass the C4 streaming metric at 191 RPS per L4 (13.9 / 14.2 / 14.3 ms, 3 of 3) = $5.80 per qualified RPS-month on-demand; with two guards per gateway the gateway's loops bind first (C4 knee 298, C38). The §1.3 baseline of all-in-one G2 nodes fails C4 at every load as built and needs the C38 loop fix even to pass at 78 RPS (15.0 ms), at $1,548 per 2-L4 node. | Make gateway VMs + dedicated guard VMs (one owner process per GPU, reached over TCP) the primary production topology; size gateways by event-loop budget (C38) and guards by GPU queue (C7); GW20's "serving unit" becomes one gateway VM + N guard VMs; re-baseline §1.3 with the measured knees and costs. | HIGH | GW08, GW20 |
| C43 | GW08, GW19, §10.5.4 | Guard calls are routed independently by each worker, so every guard owner sees near-random (Poisson-like) arrivals even when client traffic is evenly spaced, and a serial batch-1 GPU queue punishes that burstiness: per-guard capacity inside a multi-guard fleet fell to ≈ 93 RPS per L4 at the C4 limit (3 gateways + 5 guards: 466 RPS, GPUs 28% busy, owner queue p99 6.5 ms) — half of one guard's constant-arrival knee (191) and close to its random-arrival knee (98 RPS, 3 of 3); with the loop fix, 3 gateways + 4 guards reached 520 RPS (130 per guard). | Balance guard calls at fleet level (least-outstanding-work across owners with shared state, or a pooled queue per GPU pool) and size guards against the random-arrival knee; publish per-guard capacity under Poisson arrivals as the planning number. | HIGH | GW08, GW19 |

## 0.4 New cards required by the corrections

### GW14b — Console and control-plane contract v2 (C3)

| Field | Value |
|---|---|
| Phase | Observability / Surface |
| Depends on | GW05, GW13, GW14 |
| Required by | UI05, UI06, UI07, UI12, T22, GW21, GW24 |
| Primary owner | Backend + control-plane + frontend engineers |
| Objective | Keep every console and control-plane feature working across the v1 → v2 cutover by freezing and versioning the second customer-visible contract. |

**Why this card exists.** The console and control plane consume gateway data through six channels that §10.2.1 unfreezes. UI06 (versioned adapter) and UI12 (reconcile against v2) cover the browser end; no card owns the control-plane middle tier, and UI12 would only surface its breakage on GW23's critical path. The channels: telemetry → control-plane drain → EnforcementEvent (12 console endpoints plus notifications, incidents and gateway stats), pipeline_trace (137 keys, 87 read by the UI, v1's nine-stage order hard-coded), stage timings, the `zeroshield` body object and 10 X-ZeroShield-* headers (invisible to the OpenAI SDK and therefore to C1 and the WIRE bucket), control↔gateway admin HTTP (breaker, RAG collections, db/bedrock tests, MCP discover/call/oauth mirror, record-event callback, instance heartbeat), and 65 console-written config fields across 16 Redis names. The frontend reads 135 distinct gateway-produced fields and has zero references to v2's decision vocabulary.

**Implementation work.**
- Publish a machine-readable inventory of every consumed field and endpoint (evidence: `frontend-verifier/fields`, `cp-consumers`, `config-keys`).
- Define `DecisionRecord` v1 as a versioned JSON Schema (`schema_version` field) that is the v2 audit record plus the projection the console renders; producers and consumers are validated against it in CI.
- Build the control-plane ingestion adapter (v2 audit sink → EnforcementEvent or its successor). Remove the silent defaults in the current translation (event_type "request", module_id "1.1", risk 0).
- Decide, field by field, whether the `zeroshield` object and X-ZeroShield headers are kept (then frozen and added to the parity differ as a CONSOLE bucket) or versioned (then the console migrates first).
- Provide v2 equivalents of the admin endpoints the console uses, or retire the feature with a signed product decision.
- Own the writer side of GW05: a field-by-field mapping of the 65 FirewallConfig fields to Rule/Mode/Action or an explicit "unsupported" rejected at save; an API exposing the applied plan version per replica (UI07 "applied gateway policy version").
- During GW21 shadow, v2 records go to a separate stream (no double counting) that the console can display side by side.

**Live acceptance tests — required to exit.**

| Test | Live procedure | PASS | FAIL / stop |
|---|---|---|---|
| L14b-1 | Replay the C2 corpus through v1 and v2; query the 12 EventStore endpoints plus notifications, incidents, gateway stats. | Identical values within declared tolerance, or each difference is a pre-registered ledger entry. | Any endpoint silently changes meaning or returns defaults. |
| L14b-2 | Open Scan Detail for ALLOW/REDACT/BLOCK/FLAG/UNAVAILABLE fixtures. | Stages executed/skipped/unavailable, deciding rules, plan version and the C4 timing split render from the record. | Any field default-filled or recomputed in the browser. |
| L14b-3 | Playwright on the production bundle: simulators, Stage Timeline, MCP connector, RAG collections, breaker controls against v2. | Every feature works or is retired with sign-off. | Silent breakage. |
| L14b-4 | Emit a field not in the schema; read a field that is absent. | CI fails on both. | Drift passes. |
| L14b-5 | Save an unsupported combination in the console. | Rejected at save with the compiler's field-level message shown in the UI. | Generic "HTTP 400" or silent acceptance. |
| L14b-6 | Publish a plan at 70% load. | The applied-version API reflects every replica within the GW05 freshness bound. | UI shows success while a replica serves the old version. |

### GW16b — Production edge (C16, C20; absorbs T16)

| Field | Value |
|---|---|
| Phase | Hardening |
| Depends on | GW12, GW19 |
| Required by | GW20, GW22 |
| Objective | Make the edge transparent to streaming and never the capacity limit. |

**Why.** Measured: without an `upstream` block the repository's nginx opens 0.9986 new TCP connections per request and fails with HTTP 502 above ~420 req/s per edge→gateway pair (ephemeral-port exhaustion inside the container network namespace; host sysctls do not reach it). With upstream keepalive the same edge sustained ≥ 30,000 req/s at p99 < 0.4 ms. SSE is protected by `X-Accel-Buffering: no`, not by `proxy_buffering`.

**Tests.** L16b-1 upstream connections per request ≤ 0.01 at the qualified plateau · L16b-2 edge sustains ≥ 2× the fleet's qualified RPS adding < 1 ms p99 · L16b-3 fixed-interval SSE: every token arrives in its own read, max hold ≤ 1 ms, 30 ms injected hold detected · L16b-4 healthy stream > 350 s · L16b-5 drain one gateway during active streams per contract · L16b-6 unknown Host rejected, security headers present · L16b-7 regression gate: removing keepalive fails CI's edge test.

## 0.5 Corrected task index (replaces the §10.8 "Depends on" column)

Verified acyclic; every exit artifact is produced by a card in the depending card's closure. T24 moves before GW23 (C9). New cards: GW14b, GW16b. (The control-plane and worker external-AI work stays with GW24, which T08/T25 already route there; C13 itemizes it.)

| Card | Depends on |
|---|---|
| GW00 | — |
| GW01 | GW00, T02 |
| GW02 | GW00, GW01, T02, T04 |
| GW03 | GW00 |
| GW04 | GW00 (with the C1 type placement) |
| GW05 | GW04 |
| GW06 | GW03, GW04, GW05 |
| GW07 | GW04, GW05 |
| GW08 | GW03, GW04, GW07 |
| GW09 | GW02, GW08 |
| GW10 | GW02, GW08, T04 |
| GW11 | GW02, GW03, GW07, GW08 |
| GW12 | GW01, GW03, GW08, GW11 |
| GW13 | GW07, GW09, GW10, GW12 |
| GW14 | GW02, GW04, GW06, GW08, GW12, GW13 |
| GW14b | GW05, GW13, GW14 |
| GW15 | GW01, GW02, GW06, GW09, GW10, GW11, GW13, GW14 |
| GW16, GW17, GW18 | GW15 |
| GW16b | GW12, GW19 |
| GW19 | GW06, GW08, GW12, GW15 |
| GW20 | GW14, GW15, GW16, GW16b, GW17, GW18, GW19 |
| GW21 | GW02, GW14b, GW20 |
| GW22 | GW21, T21 |
| T20 | GW20 |
| T21 | GW19, GW20, T20 |
| T22 | GW01, GW05, GW07, GW13, GW14b, T20, UI12 |
| T23 | T04, GW01, T20, T21, T22 |
| T24 | GW22, T23 |
| GW23 | GW22, T20, T21, T22, T23, T24 |
| GW24 | GW18, GW23 |
| UI05, UI06 | UI03, GW14, GW14b |
| UI07 | UI03, GW05, GW07, GW13, GW14b |
| UI12 | UI11, GW14b, GW20, T20, T21 |
| UI13 | UI12, T22, GW23 |

Relocated tests (the need is satisfied only by a later card): LGW03-5 → GW19 · LGW04-2, LGW04-4 → GW15 · LGW05-3 → GW20 · LGW05-7 (console part) → GW14b/UI07 · LGW07-1, LGW07-4 → GW15 · LGW08-1 → GW15 · LGW08-6 → GW19 · LGW09-2 → GW15 · LGW11-3 (real redactor) → GW15 · LGW12-7 → GW15 · LGW12-8 → GW13 · LGW13-5 → GW20 · LGW19-1 → GW20.

# 1. Program definition and release equation

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p><strong>Final objective</strong></p>
<p>Build a horizontally scalable, low-latency AI security gateway whose code has no capacity assumption tied to the present $5,000 budget. Within the current budget, maximize qualified RPS while keeping p99 complete firewall overhead below 20 ms and preserving every enabled policy, SSE/OpenAI SDK semantics, tenant isolation, failure safety and auditable user control.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 1.1 What counts as qualified throughput

- A qualified request is a real /v1 request through the production-shaped gateway path with authentication, organization configuration, applicable policy work, selected input controls, customer-provider dispatch for allow/redact traffic, selected output controls, finalization and audit enqueue.

- Policy blocks are correctness traffic and are reported separately; they do not inflate the all-permitted capacity headline.

- Health checks, scan-only calls, stubs, cached repeated prompts, skipped guards, malformed streams, 429/503s, cancelled calls and partial streams are not successful qualified completions.

- Real BYOK provider capacity is reported separately from controlled-provider firewall capacity. A customer provider quota is not AI Mesh gateway capacity.

## 1.2 Latency contract

> **v2.1 CORRECTION (C4).** Streaming output redaction must hold back any suffix that could still become a match until the next upstream token arrives, so part of T_release_lag is *waiting for the provider*, not firewall work, and it is bounded below by the provider's inter-token latency. Measured: v1 releases first content after ≈ 38–42 tokens × ITL; the v2 prototype's minimal pattern-aware holdback adds 1–2 inter-token intervals per word and is unbounded for URL/UUID/base64 runs. T_release_lag is therefore split into **T_release_processing** (gateway compute between an upstream chunk's arrival and its safe downstream write, excluding waits for further upstream bytes) — inside the p99 < 20 ms SLO together with T_input and T_finalize — and **T_holdback_wait** (time a released byte spent waiting for disambiguating upstream bytes), published separately with a signed bound per streaming mode (proposed: for word-class patterns ≤ 2 upstream tokens at p50 and ≤ 3 at p99 per stream — the prototype measured 40 / 60 ms at ITL 20 ms because '.' can continue an e-mail address — with a byte ceiling for unbroken runs, C25). Holdback applies only when the pinned plan has an enforcing output rule. Requires owner sign-off in the re-signed T01 contract.


| **Metric** | **Definition** | **Release rule** |
|----|----|----|
| T_input | Owned time from request acceptance before auth through final input decision and provider-dispatch readiness. | Measured separately; included in total firewall overhead. |
| T_release_lag | For SSE: upstream content-ready → corresponding safe downstream write completion, including buffering/guard work. | p99 and max recorded; cannot be hidden as provider time. |
| T_finalize | Last upstream content-ready → terminal downstream completion/audit enqueue. | Included. |
| T_fw_addon | Complete AI-Mesh-added critical-path time, excluding intrinsic customer-model generation. | p99 \<20 ms for the signed low-latency profile. |
| TTFT | Client-visible time to first content. | Reported, but not mislabeled as firewall overhead. |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p><strong>Important output-mode rule</strong></p>
<p>Strict whole-response withholding and normal streaming are different products at the wire. A strict mode may wait for generation to finish before any content is released; it cannot inherit the low-latency SSE TTFT claim. Each output mode gets its own qualification profile.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 1.3 Capacity and cost contract

> **v2.1 CORRECTION (C5, C18).** "4 × G2 / 8 × L4 / ~1,064 RPS / ~$4,561" is not a measured baseline: 1,064 RPS is derived from vendor DeBERTa figures bridged to L4 (the top of its source's 590–1,064 range) and $4,561.44 is exactly the 3-year committed-use price. The same fleet and traffic cost **$8,952.24/month on-demand** in Mumbai (Cloud Billing Catalog API, 2026-09-23); like-for-like on the source's own 3-year basis it is $5,238.01 — over $5,000 on every basis checked. The "≥ 300 CPU-side RPS/vCPU" target has no source. Measured replacements are in §0.2. G2/L4 capacity was exhausted in every asia-south1 zone twice on 2026-09-23 and in two neighbouring regions: the envelope must price a reservation or qualify a fallback pool and a degraded-capacity posture.


- Current monthly infrastructure envelope: ≤\$5,000 for the AI Mesh platform infrastructure; tenant model-token spend remains separate.

- Historical reference point: 4 × G2 nodes / 8 × L4 / ~1,064 RPS / ~\$4,561 per month. Treat this as a planning baseline that must be re-priced and re-measured, not a current quote or cap.

- Primary optimization metrics: qualified RPS, RPS/L4, CPU ms/request, qualified RPS/vCPU, RPS/\$1,000-month, p99 firewall overhead, error rate, GPU queue time and release lag.

- Engineering target for the CPU gateway in isolation: demonstrate ≥300 qualified CPU-side RPS/vCPU if the signed full-security profile permits; 400–500 is a stretch target, not an assumed result.

- 100k RPS remains a future scale-out architecture test only. The code must not contain a design ceiling below it; the present \$5k fleet is not expected to deliver 100k completed chats.

# 2. Final architecture doctrine

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th>Organization Console / Control API<br />
│ validate + compile<br />
▼<br />
Versioned Organization Execution Plan ──► local snapshots on gateway replicas<br />
<br />
OpenAI SDK / App<br />
│<br />
▼<br />
Edge / LB ─► Stateless AI Mesh Gateway<br />
auth → quota/KS → policy transform → selected T1 → selected local semantic guard<br />
│<br />
▼<br />
authoritative resolver<br />
ALLOW / FLAG / REDACT / BLOCK<br />
│<br />
authorized provider call<br />
│ SSE / JSON<br />
▼<br />
selected local output controls<br />
│<br />
▼<br />
Client<br />
<br />
Local guard inference scales independently (CPU/GPU).<br />
Audit/telemetry/analytics are asynchronous and off the synchronous chat decision path.</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 2.1 Non-negotiable scalable-code rules

> **v2.1 ADDITIONS (C7, C23, C25, C31).** Rules added, each proven by measurement: (1) **One process owns each accelerator** — N sessions on one L4 gave zero capacity at p99 ≤ 10 ms; MPS was worse. (2) **Admission is enforced at every shared resource, per tenant** — per-worker bounds do not bound a shared GPU queue (overload became UNAVAILABLE → FAIL_OPEN tenants unscanned), and a tenant-blind FIFO let one tenant's long prompt shed another's traffic 30/30. (3) **No framework middleware and no synchronous I/O on serving loops** — four BaseHTTPMiddleware layers cut one worker's throughput 16×; a synchronous metrics-file write stalled a guard owner 200–380 ms every 10 minutes. (4) **Work is linear in bytes** — scanning must not rescan held content (a quadratic holdback cost 17.9 s CPU for 32 KiB of base64). (5) **Streams never wait for CPU work on their loop** (C38) — tokenization, scanning and metrics exposition on the same event loop pushed the worst chunk of each stream past 20 ms at p99 from 63–78 RPS per g2-standard-24 while the GPU was ≤ 15% busy; CPU-bound work runs in GIL-releasing executors or separate processes and loop lag is an SLO input.


| **Rule** | **Required design** |
|----|----|
| No budget-coded ceilings | No MAX_RPS=1064, fixed GPU count, fixed workers=4, or queue sizes derived from today’s topology. Resource bounds come from cgroups, memory, FDs, measured service rate and deployment configuration. |
| Stateless serving replicas | No unique tenant policy or durable state lives only inside one gateway. Replicas are disposable. |
| Compiled org plan | User configuration is validated and compiled outside the request hot path into a versioned execution plan. |
| One enforcement authority | Detectors emit findings; one resolver applies the organization’s selected rules/actions. Detectors do not independently invent final transport outcomes. |
| Local guard interface | Gateway code invokes a stable local guard interface; deployment can scale from one accelerator to many without editing scanner semantics. |
| Bounded everything | Request queues, guard queues, connection pools, SSE buffers, audit queues and retries are bounded and observable. Bounds scale with resources; they are not unbounded or magic constants. |
| Async I/O | Provider waits, SSE sockets, audit enqueue and shared-state operations must not occupy one OS thread per in-flight chat. |
| Control/data separation | Cloud SQL/analytics/dashboard work stays off the synchronous /v1 path; policy/config arrives by snapshots/versioning. |
| No hidden fallback | Missing local model, missing tenant config or breaker-open state must never masquerade as a successful benign scan. |

## 2.2 Organization authority model

| **Dimension** | **Organization controls** | **Platform retains** |
|----|----|----|
| Detection | Enable/disable supported content detectors; select semantic features and thresholds within validated ranges. | Whether auth, tenant isolation, request framing, resource limits and cross-tenant protections exist. |
| Mode | OFF, MONITOR, ENFORCE for supported content rules. | Truthful reporting of skipped/unavailable checks. |
| Action | ALLOW, FLAG, REDACT, BLOCK, REWRITE where the capability genuinely supports it. | Schema validation, safe transformation requirement, and no false claim that an unavailable detector ran. |
| Failure posture | Select from explicitly supported fail behavior for optional content controls. | Missing auth/policy identity and platform-integrity failures cannot inherit another tenant or silently allow. |
| Streaming mode | Select qualified streaming vs strict whole-response mode where offered. | Wire semantics must match the selected mode; already-released bytes cannot be retroactively blocked. |

# 3. Required live environments and proof levels

| **Level** | **Topology** | **Purpose** | **Exit authority** |
|----|----|----|----|
| L0 — source baseline | Pinned Git commit + image/build provenance. | Know exactly what code and artifacts are being evaluated. | No performance claim. |
| L1 — immutable Docker staging | Gateway + control + frontend + Redis/Valkey + test DB + local guard runtime + nginx + synthetic OpenAI provider recorder. | Functional wiring, user actions, bytes, SSE, SDK, timing algebra, failure injection. | Closes most correctness tasks. |
| L2 — GPU staging | Release Docker images on the target GPU/runtime host; same synthetic provider. | Actual local-model placement, queue/batch curves, per-unit latency/capacity. | Closes local inference and single-unit capacity. |
| L3 — multi-replica staging | 2→4 gateway replicas, multiple guard replicas/GPUs, shared state, production-shaped edge. | Horizontal scaling, tenant isolation, shared-state bottlenecks, drains/failover. | Closes scale and resilience architecture. |
| L4 — production-like qualification | Frozen candidate, in-region load generators, production-shaped network, \$5k-class resource envelope. | 30-minute plateaus, 2-hour soak, cost-qualified max RPS. | Primary release SLO. |
| L5 — real-provider canary | Authorized organization/provider with bounded traffic. | SDK/provider compatibility and real-generation behavior. | Does not redefine gateway capacity beyond tested provider quota. |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p><strong>Live-test rule</strong></p>
<p>Unit tests may protect regressions, but they do not close a task in this runbook. Every exit gate below names the live Docker/staging proof that must pass.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 4. Execution order

**Frontend revamp integration:** UI00-UI13 begins after T00/T01, can progress in parallel with backend work, and must reach UI12 before T22/T23 can pass. The redesign never substitutes mock data or visual polish for backend truth.

| **Task** | **Title** | **Phase** |
|----|----|----|
| T00 | Freeze source/deployment identity and close critical repository/security unknowns | Foundation |
| T01 | Sign the final contracts: SLO, budget, user authority, output modes and scalable-code doctrine | Foundation |
| T02 | Build the immutable Docker/staging lab, synthetic provider recorder and evidence harness | Foundation |
| T03 | Repair end-to-end timing and benchmark eligibility | Foundation |
| T04 | Freeze the capability corpus and quality gates | Foundation |
| T05 | Compile organization policy into one versioned execution plan | Core correctness |
| T06 | Make one authoritative enforcement resolver control provider dispatch and output disposition | Core correctness |
| T07 | Make redaction/Tier-1 fast, deterministic and byte-verifiable | Core correctness |
| T08 | Establish a zero-platform-external-AI boundary | Local AI |
| T09 | Productionize local input semantic inference and lifecycle | Local AI |
| T10 | Productionize output security and explicit streaming/strict modes | Local AI |
| T11 | Prove SSE protocol correctness under normal, slow, malformed and failed streams | Protocol |
| T12 | Prove OpenAI Python/Node SDK compatibility against live staging | Protocol |
| T13 | Make routing, retry, fallback, cancellation and provider errors stream-safe | Protocol |
| T14 | Reduce auth/quota/kill-switch/shared-state latency without losing truth | Hot path |
| T15 | Move telemetry/analytics off the synchronous request path | Hot path |
| T16 | Harden the public edge, connection reuse, timeouts and long-lived SSE | Hot path |
| T17 | Implement resource-aware admission, bounded queues and backpressure | Hot path |
| T18 | Measure one serving unit: p99, RPS/vCPU, RPS/L4 and safe saturation point | Performance |
| T19 | Prove horizontal scaling with no application-code changes | Performance |
| T20 | Optimize and qualify the ≤\$5k production envelope | Performance |
| T21 | Run resilience/chaos and rollback drills under load | Resilience |
| T22 | Prove real frontend control and tenant actions under concurrent load | Resilience |
| T23 | Run final staging qualification: repeated plateaus + soak + cost reconciliation | Release |
| T24 | Canary with an authorized real provider and customer-shaped traffic | Release |
| T25 | Remove legacy Bedrock/Vertex/Gemini paths, credentials and regression escape hatches | Release |
| T26 | Publish reproducible evidence, capacity limits and scale-up handoff | Release |

**T00 Freeze source/deployment identity and close critical repository/security unknowns**

| **Field** | **Value** |
|----|----|
| Phase | Foundation |
| Depends on | None |
| Primary owner | Backend lead + DevOps + security owner |
| Objective | Establish exactly which code, images, frontend build, policies, model artifacts and credentials will be tested; resolve any potentially exposed credential before new staging credentials are issued. |

### Why this task exists

Previous reviews found branch divergence and a potential tracked SSH private-key artifact. Performance or security work is invalid if the tested checkout is not the deployed build or if credentials may already be exposed.

### Implementation work

1.  Pin the execution branch/commit and record the full SHA. Re-fetch the current GitHub head; do not assume the September 12 reviewed commit is still the latest candidate.

2.  Build a source→image→container→frontend map: OCI digest/revision label, lockfile hashes, frontend asset hash, compose/Helm/IaC inputs, model/tokenizer/export hashes and rollback tuple.

3.  Privately classify the tracked OpenSSH-looking artifact. If genuine or uncertain, revoke/rotate the corresponding authorization before continuing and create a repository-history remediation ticket.

4.  Identify two synthetic organizations with opposite policies, one synthetic OpenAI-compatible provider recorder, the approved GPU staging host/cluster, and the approved load-generator network.

5.  Record current cloud topology/cost only as observed. Historical GCP/AWS prices and quotas are evidence, not current truth.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L00-1 | From a clean checkout, build the candidate images and start a read-only staging copy; correlate a browser request ID to the expected gateway/container digest. | Browser/API/container all map to the same commit and build manifest. | Any stale frontend, bind-mounted source, unknown image revision or mixed environment. |
| L00-2 | Recreate the staging stack on a second clean worker/VM from the manifest. | Same image digests and functional smoke behavior. | Manual package installs, docker cp, or undocumented local files required. |
| L00-3 | Attempt login/use with the old/rotated test credential if applicable. | Old credential rejected; authorized replacement works. | Potentially exposed key remains trusted or provenance cannot be established. |

### Exit criteria

- Pinned full commit SHA and immutable image tuple.

- Source/image/frontend/model manifest complete.

- Credential issue disposition recorded and, if needed, rotation verified.

- Authorized synthetic tenants, recorder and rollback artifacts identified.

### Evidence package

- environment-manifest.json

- image-digest and frontend-build report

- credential disposition record (private)

- source→runtime route map

- rollback tuple

### Rollback / stop rule

No product behavior changes belong in T00. If identity cannot be proven, stop the program rather than optimizing an unknown deployment.

**T01 Sign the final contracts: SLO, budget, user authority, output modes and scalable-code doctrine**

> **v2.1 CORRECTION (C16, C32).** The existing acceptance-contract.json was signed by an AI agent ("cursor-grok-t01"), not by the product owner, security owner and backend/ML leads named here; quality is "UNSIGNED until T04"; the $5k envelope, failure postures, freshness/kill-switch/revocation bounds, the 429/503/queue policy, drain and the Node SDK version are absent; p99 and the throughput floor are NOT_MEASURED. Re-open T01 and re-sign it with: the C4 timing definition, the C5 measured capacity and explicit pricing basis, the C6 quality floors, and a **traffic-shape assumption register** (multi-turn share, languages/scripts, system-prompt use, output shapes, tenant count and skew, generation lengths, client retry behaviour) — there is no production traffic, so every capacity and quality claim rests on these assumptions and T24 must test them.


| **Field** | **Value** |
|----|----|
| Phase | Foundation |
| Depends on | T00 |
| Primary owner | Product owner + security owner + backend/ML leads |
| Objective | Freeze the rules that performance work is not allowed to redefine after results are seen. |

### Why this task exists

Without a signed contract, a failing run can be made green by skipping a guard, changing a threshold, changing what \<20 ms means or reclassifying a user action.

### Implementation work

6.  Sign p99 \<20 ms complete firewall overhead for the low-latency profile; define exact input token bands, output mode, languages, allowed buffering and error ceiling.

7.  Sign the current ≤\$5,000/month deployment envelope and state explicitly that it is not encoded as an application capacity constant. Historical 1,064 RPS becomes the minimum planning floor to beat, not a cap.

8.  Define qualified RPS, real-provider RPS, mixed-policy evaluation throughput, RPS/vCPU and RPS/L4 as separate metrics.

9.  Freeze organization-controlled content actions: OFF/MONITOR/ENFORCE plus ALLOW/FLAG/REDACT/BLOCK/REWRITE only where supported. Define platform-integrity controls that remain mandatory.

10. Define output modes: streaming/pre-release processing versus strict whole-response withholding. Declare which mode is covered by the \<20 ms headline.

11. Freeze quality thresholds, failure postures, overload behavior, queue limits policy and accepted versions of OpenAI Python/Node SDKs for conformance.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L01-1 | In live staging, save each supported action/mode in the real UI/API, reload, and read the compiled gateway policy version. | Stored value, compiled plan and runtime behavior mapping are unambiguous. | UI label exists but no runtime mapping, or conflicting semantics remain. |
| L01-2 | Create an intentionally unsupported capability/action combination. | Control plane rejects it explicitly. | It saves successfully and silently becomes allow/no-op. |
| L01-3 | Attempt to set a content rule OFF while platform auth/tenant isolation remains enabled. | Content rule is disabled; platform integrity remains enforced. | Content rule secretly still acts, or tenant can disable service integrity. |

### Exit criteria

- Signed acceptance-contract.json with non-null thresholds.

- Action/mode/failure matrix mapped to actual API fields.

- Low-latency output mode explicitly named.

- No performance or quality threshold left to choose after benchmarking.

### Evidence package

- signed contract

- action matrix

- capability ledger

- SDK version matrix

- budget/scaling doctrine

### Rollback / stop rule

Restore test-tenant settings after validation. Contract changes require a versioned sign-off, never an informal benchmark exception.

**T02 Build the immutable Docker/staging lab, synthetic provider recorder and evidence harness**

| **Field** | **Value** |
|----|----|
| Phase | Foundation |
| Depends on | T00,T01 |
| Primary owner | DevOps + QA + backend performance engineer |
| Objective | Create a production-shaped environment where real bytes, timing, SDK behavior and faults can be observed safely. |

### Why this task exists

The final gates require real containers and wire behavior. Jupyter microbenchmarks or mocked HTTP handlers cannot prove the gateway.

### Implementation work

12. Create a separate staging project/namespace with candidate gateway, control, baked frontend, nginx/edge, Redis/Valkey, test DB, local guard runtime, audit sink and synthetic provider recorder.

13. Build all images from pinned lockfiles; no source bind mounts for release proof. Record image digests and OCI revision labels.

14. Implement an authenticated staging-only OpenAI-compatible provider that can emit deterministic JSON/SSE, tools, delays, malformed events, disconnects and input capture; expose its fault controls only inside staging.

15. Implement a request-ID-correlated evidence collector recording provider input bytes, SSE receive/release events, guard calls, policy/model versions, queue timings and final action without printing synthetic secrets into normal metrics.

16. Provide an open-loop backend load generator capable of exceeding the intended staging capacity, plus small Playwright/browser smoke tests. Browser load is never the primary performance generator.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L02-1 | Destroy and recreate the staging project from clean images/config, preserving only approved test data. | Stack returns healthy and all image/model hashes match manifest. | Manual repair inside containers required. |
| L02-2 | Send one ALLOW, one REDACT and one BLOCK through the real edge. | Recorder sees one original-safe call, one sanitized call, and zero calls respectively. | Labels pass while bytes/call counts disagree. |
| L02-3 | Start the same candidate on a second staging host/namespace. | Core smoke outcomes are identical. | Environment depends on hidden host state. |
| L02-4 | Attempt to call fault-control endpoints from production-shaped public route/wrong tenant. | Denied/not installed. | Fault controls are remotely reachable. |

### Exit criteria

- One-command documented immutable staging recreation.

- Synthetic provider/recorder and open-loop loadgen operational.

- Request IDs join browser→gateway→guard→provider→audit.

- No production/customer data used.

### Evidence package

- staging topology

- compose/Helm manifests

- image/model digests

- recorder schema

- recreation log

- request-ID correlation bundle

### Rollback / stop rule

Restore previous staging image tuple. Never use destructive volume deletion against shared/prod state.

**T03 Repair end-to-end timing and benchmark eligibility**

| **Field** | **Value** |
|----|----|
| Phase | Foundation |
| Depends on | T00,T01,T02 |
| Primary owner | Instrumentation engineer + performance QA |
| Objective | Make the \<20 ms number falsifiable and resistant to provider TTFT, buffering and hidden failure contamination. |

### Why this task exists

Earlier code and plans showed TTFT-contaminated addon calculations and clamped residuals. Every later performance claim depends on fixing this first.

### Implementation work

17. Start the monotonic request epoch before authentication middleware work and record every owned interval: auth, shared state, policy, T1, guard queue/execution, dispatch, output scan/hold/release and finalization.

18. Record provider intrinsic token-ready timing from the synthetic provider independently of gateway backpressure. Do not subtract cross-host absolute timestamps without bounded synchronization.

19. Keep signed reconciliation residuals; invalid/missing timestamps fail the run. Do not max(0) a negative residual.

20. Add eligibility flags: firewall-tax eligible, gateway-capacity eligible, real-provider eligible, expected-policy-block, incomplete-stream, cached, scan-only, degraded.

21. Expose the same authoritative metrics in Scan Detail without recomputing different math in the frontend.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L03-1 | Provider TTFT sweep 50 ms → 500 ms → 2,000 ms with identical firewall work. | Reported firewall overhead remains approximately stable while client TTFT changes. | Firewall overhead tracks provider TTFT. |
| L03-2 | Inject exactly 5 ms before provider dispatch and 7 ms in output enforcement. | ~12 ms appears in firewall-owned timing within frozen tolerance. | Injected work is missing or attributed to provider. |
| L03-3 | Inject a 30 ms hold in the middle of SSE. | Release-lag metric shows the hold and low-latency profile fails. | Hold disappears inside provider/model time. |
| L03-4 | Delete one required timestamp / force a negative residual. | Harness marks run INVALID. | Metric clamps to zero/green. |
| L03-5 | Slow downstream client while producer remains fast. | Backpressure time is distinguished from model compute and appears in release metrics. | Slow client makes provider appear slow or disappears. |

### Exit criteria

- Provider delay cannot contaminate firewall tax.

- Middle-stream holdback is visible.

- All started requests remain in failure/deadline accounting.

- Frontend and backend metrics reconcile within frozen tolerance.

### Evidence package

- timing contract

- raw per-request event log

- anti-cheating test captures

- residual histograms

- frontend reconciliation screenshots

### Rollback / stop rule

If valid instrumentation is reverted, all performance claims are suspended. Never restore a known-invalid metric for convenience.

**T04 Freeze the capability corpus and quality gates**

> **v2.1 CORRECTION (C6).** T04 has not run (no evidence directory; L04-1..4 never executed). The current corpus (tests/detection_corpus: 323 malicious / 425 benign) is all 40–93 ASCII characters and single-window, has no PII/secret, encoded, multilingual, long or chunk-boundary items and no span/action labels, and 292 of the 323 malicious items were authored against v1's own regex patterns. Validation showed why each gap matters: one benign developer SQL item scores 0.99 on PG2 and produced almost every long-text false positive; benign confidentiality system prompts score 0.998; native-script Indic injections are missed 12/12 by PG2-22M; homoglyphs drop a 0.999 attack to 0.0015; indirect instructions in retrieved documents are caught 1/4. Add long and multi-window benign text (W = 1, 2, 4, 7), long attacks with the payload in the first/middle/last window, multilingual and code-mixed families (Hindi, Marathi, Bengali, Tamil, Telugu, Gujarati, Kannada, Malayalam, Punjabi, Urdu, Hinglish), obfuscation (homoglyphs, leetspeak, encodings), developer SQL/code, real system prompts and multi-turn transcripts, indirect-injection documents, PII/secret families with format-preserving values and span labels, and a frozen held-out hash artifact.


| **Field** | **Value** |
|----|----|
| Phase | Foundation |
| Depends on | T01,T02 |
| Primary owner | Security QA + ML engineer + reviewer |
| Objective | Prove the fast local stack actually detects/redacts the supported behaviors before latency optimization is allowed to ship. |

### Why this task exists

Speed cannot compensate for a detector that misses attacks or over-blocks normal developer traffic. Earlier evidence found both false negatives and false positives in deterministic rules.

### Implementation work

22. Build and version a held-out corpus covering injection/jailbreak paraphrases, benign developer/code prompts, PII/secrets/credentials, encoded/split data, tools/MCP, multilingual cases, long inputs and output chunk-boundary cases.

23. Label expected findings, spans, organization policy, actual final action and provider-byte outcome. Use synthetic values only.

24. Freeze calibration and held-out partitions before tuning thresholds. Report request-level FPR/recall; do not infer request FPR by multiplying per-window assumptions.

25. Map every user-visible detector/action to a named local capability and test family. Unsupported capability remains unavailable, not green.

26. Keep PG2 injection classification separate from output harm taxonomy, sensitive-span detection, grounding and rewrite semantics.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L04-1 | Replay a held-out sample through live staging, not direct model calls. | Final action and provider bytes match labels. | Model score is correct but gateway enforcement is wrong. |
| L04-2 | Replay benign inline code/tool descriptions at low and moderate load. | No unapproved terminal false blocks. | Legacy broad rule still blocks benign developer traffic. |
| L04-3 | Replay encoded/split secret fixtures across JSON fields and SSE boundaries. | Expected transforms/blocks occur with correct spans. | Fast path loses coverage or corrupts UTF-8. |
| L04-4 | Replay long/middle attacks across window counts. | Every approved input region is evaluated or rejected explicitly. | Quiet truncation/head-tail shortcut. |

### Exit criteria

- Held-out corpus hash frozen.

- Numerical quality gates signed by category/language.

- Every advertised policy has a local capability owner.

- No benchmark result can change labels/thresholds retroactively.

### Evidence package

- corpus manifest

- label audit

- held-out hashes

- live replay report

- capability coverage map

### Rollback / stop rule

Do not rewrite held-out labels to make a candidate pass. Revert detector changes or mark the capability unsupported.

**T05 Compile organization policy into one versioned execution plan**

| **Field** | **Value** |
|----|----|
| Phase | Core correctness |
| Depends on | T01,T02,T04 |
| Primary owner | Backend/control-plane engineer + security reviewer |
| Objective | Make the organization the authoritative owner of supported content-security behavior while eliminating scattered per-module config interpretation. |

### Why this task exists

Current code has multiple defaulting paths and historical platform floors. A single compiled plan reduces latency, race conditions and inconsistent semantics.

### Implementation work

27. Define a versioned ExecutionPlan per organization: enabled detectors, modes, actions, thresholds, output mode, failure posture, allowed models and routing constraints.

28. Compile/validate outside the request hot path; gateways receive immutable snapshots and pin one plan version per request.

29. OFF means no work/effect needed solely for that optional rule. MONITOR means evaluate and record without that rule enforcing BLOCK/REDACT. ENFORCE applies the selected action.

30. Distinguish explicitly empty valid config from missing/unavailable config. Never inherit another tenant/default plan for a known organization.

31. Keep auth, tenant isolation, request limits and cross-tenant protections outside tenant-disablable content policy.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L05-1 | For one detector, transition OFF→MONITOR→ENFORCE→OFF through the real frontend; send the same request after each change. | Detector call counts, final action and policy version change exactly as configured. | Switch saves but runtime behavior does not change or hidden floor persists. |
| L05-2 | Two tenants use opposite actions on identical content at the same time. | Each gets its own action and compiled version; no cross-talk. | Global mutable threshold/action contaminates the other tenant. |
| L05-3 | Interrupt policy/config sync and restart one gateway replica. | Replica is not-ready/uses signed last-good behavior; never falls to another tenant/default. | Scanless allow or foreign config. |
| L05-4 | Miss one update notification and leave traffic running. | Periodic reconcile converges within signed freshness bound. | Stale plan persists indefinitely. |

### Exit criteria

- Single runtime source of truth for organization content policy.

- Two-tenant concurrency and refresh tests pass.

- Plan version appears in every trace/audit decision.

- No optional rule can secretly enforce when OFF.

### Evidence package

- ExecutionPlan schema

- policy version timeline

- two-tenant captures

- missed-update recovery report

- UI persistence proof

### Rollback / stop rule

Tenant isolation/config-readiness fixes are not rollback candidates. Contain traffic or use the last known secure build if availability regresses.

**T06 Make one authoritative enforcement resolver control provider dispatch and output disposition**

| **Field** | **Value** |
|----|----|
| Phase | Core correctness |
| Depends on | T04,T05 |
| Primary owner | Backend/security engineer |
| Objective | Guarantee that detector findings cannot be discarded or independently rewritten before the final action. |

### Why this task exists

Repository review found cases where semantic findings could be filtered before enforcement and output degraded/block semantics could change across layers.

### Implementation work

32. Normalize all detector results into findings with detector/version/category/score/span/status provenance; detectors do not send HTTP responses themselves.

33. Pass findings and the pinned ExecutionPlan once into the resolver. Resolve conflicts by signed rule priority/action semantics, not a global severity max that overrides explicit user intent.

34. Make input terminal BLOCK happen before any provider dispatch. REDACT validates transformed bytes before dispatch. FLAG remains metadata/review state plus the actual transport outcome.

35. For output, propagate the resolved action through sanitizer/stream wrapper without later downgrades or contradictory raw verdict fields.

36. Persist exactly one authoritative decision record per request phase and correlate it with provider/client bytes.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L06-1 | Enabled semantic injection returns BLOCK with no regex-policy match. | Provider recorder receives zero calls; final trace says BLOCK from the correct rule/model. | Finding disappears and request is allowed. |
| L06-2 | Same finding with organization action FLAG. | Provider is called; finding is present; transport action is allow + flagged. | Model recommendation silently becomes BLOCK. |
| L06-3 | PII=REDACT plus Credentials=BLOCK on one prompt. | Final transport BLOCK; no provider call; both findings retained. | Redaction cancels independent block or duplicate terminal writers. |
| L06-4 | Output model reports terminal BLOCK plus degraded metadata. | Composed output pipeline preserves signed terminal behavior. | Later generic degraded branch converts it to a different action without policy authority. |

### Exit criteria

- Input semantic findings survive to resolver.

- Provider call count/bytes always match final input action.

- Output resolved action survives to wire.

- Single authoritative audit decision.

### Evidence package

- action matrix live report

- provider recorder calls

- output wire captures

- audit joins

- policy/detector version proof

### Rollback / stop rule

Revert to last validated resolver path. Do not route around the resolver to restore availability.

**T07 Make redaction and Tier-1 fast, deterministic and byte-verifiable**

| **Field** | **Value** |
|----|----|
| Phase | Core correctness |
| Depends on | T03,T04,T06 |
| Primary owner | Performance/backend + data-protection engineer |
| Objective | Remove repeated Python/regex work while preserving original-byte correctness and organization-selected actions. |

### Why this task exists

Tier-1 and repeated redaction were historical CPU ceilings. Fast approximate matching is unsafe if Unicode semantics or spans change.

### Implementation work

37. Build one bounded canonicalization/decoding pipeline with an original-field/span map. Compile native multi-pattern structures outside request handling.

38. Demote or narrow known false-positive injection patterns according to the frozen quality contract; keep sensitive-data/credential/DoS/transport findings where selected.

39. Use native/Hyperscan-like matching as candidate discovery only when exact verification is needed. Validate Unicode/word-class semantics rather than assuming Python regex equivalence.

40. Merge compatible spans and transform each content version once. Remove repeated redact_all calls from traces/telemetry.

41. Bound decode depth, expansion ratio, regex work, fuzzy matching and input size. Unsafe/unmaskable mandatory redaction becomes deny, not raw pass-through.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L07-1 | Replay complete redaction corpus through live gateway/recorder before and after the new engine. | Mandatory spans and final provider bytes are equivalent except signed behavior changes. | Any unapproved leak/span corruption. |
| L07-2 | Send Unicode thin spaces, confusables, percent/HTML encoding and split secrets. | Expected findings and sanitized bytes. | ASCII-only port bypasses cases. |
| L07-3 | Saturate Tier-1 with short/512/1024-token unique prompts. | CPU ms/request and tail improve without quality loss or thread explosion. | Throughput improves only by skipped checks or unbounded worker growth. |
| L07-4 | Force redaction no-op/partial mask. | Request/output follows signed fail behavior; raw value not forwarded. | REDACT badge with unchanged bytes. |

### Exit criteria

- Mandatory corpus passes at network boundary.

- Tier-1 CPU profile materially reduced and bounded.

- Redaction applies once per content version.

- No raw synthetic secret in provider/log/UI error paths.

### Evidence package

- pattern/native-engine manifest

- before/after CPU profiles

- span/byte captures

- quality report

- redaction pass counters

### Rollback / stop rule

Keep the prior safe engine available as a controlled rollback, but do not claim the new latency profile if the slow path is silently re-enabled.

**T08 Establish a zero-platform-external-AI boundary**

| **Field** | **Value** |
|----|----|
| Phase | Local AI |
| Depends on | T00,T01,T02 |
| Primary owner | Backend + DevOps + security owner |
| Objective | Ensure platform security decisions no longer depend on Bedrock, Vertex/Gemini, hosted moderation/judges or hidden external AI fallbacks. |

### Why this task exists

The reviewed commit still routes Tier-2 to Gemini or legacy Bedrock and retains other Bedrock consumers. Zero external AI must be proven at both code and network boundaries.

### Implementation work

42. Inventory all platform AI call sites: input/output scanners, judge, embeddings/grounding, simulator, rewrite, background jobs, error/fallback paths and control-plane security engines.

43. Define the permitted customer-generation egress separately from forbidden platform guard/judge egress. An organization provider hostname cannot be treated as permission for arbitrary security calls.

44. Introduce a local capability registry/adapter. Missing local capability returns unavailable/unsupported, never an external fallback.

45. Add runtime dependency counters and egress policy so staging can deny all platform guard destinations while allowing the synthetic/customer provider.

46. Keep rewrite as a separately declared capability: deterministic/local rewrite or an explicitly user-authorized extra customer-model call outside the low-latency profile.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L08-1 | Run every in-scope surface with Bedrock/Google AI egress denied at network level. | Supported features continue locally; unsupported ones fail explicitly; zero attempted external guard calls. | AccessDenied/connection attempts appear or requests silently allow. |
| L08-2 | Exercise startup, restart, breaker, scheduled/background jobs, MCP/RAG and output failure paths. | Zero forbidden-call counters across all paths. | Hidden fallback appears only during failure/background work. |
| L08-3 | Allow only the synthetic/customer generation endpoint and send normal chat. | Exactly one permitted generation dispatch; no second judge/rewrite call unless the user explicitly selected a non-low-latency feature. | Platform AI piggybacks on the provider allowlist. |

### Exit criteria

- Complete call-site ledger.

- Network deny test shows zero forbidden attempts.

- Supported capabilities have local replacements; unsupported ones are explicit.

- No cloud-AI fallback in runtime config.

### Evidence package

- external-AI callgraph

- egress policy and counters

- denial-test traces

- capability registry

- background-path report

### Rollback / stop rule

Before final removal, use only the last validated local build or contain traffic. Do not restore a hidden cloud judge after claiming local-only enforcement.

**T09 Productionize local input semantic inference and lifecycle**

| **Field** | **Value** |
|----|----|
| Phase | Local AI |
| Depends on | T02,T03,T04,T08 |
| Primary owner | ML runtime + backend + DevOps |
| Objective | Turn the ~2.14 ms Prompt Guard TensorRT component result into a reproducible, scalable serving component with truthful queue/placement metrics. |

### Why this task exists

A notebook/session.run microbenchmark does not prove release-container placement, sustained throughput, batching behavior, queue delay or multi-window request performance.

### Implementation work

47. Pin model/tokenizer/export hashes and compare pristine reference logits/decisions against the chosen ONNX/TensorRT path over the held-out corpus.

48. Benchmark valid window counts W=1,2,3,4,7 and supported batch/concurrency combinations with H2D/D2H included; report p50/p95/p99 and windows/s. Keep tokenization separate but include it in gateway overhead.

49. Profile execution-provider graph placement separately; provider registration is not proof that major compute ran on TensorRT.

50. Define ownership: embedded process or node-local guard service. Expose queue depth, enqueue/start/end, model hash, readiness and cancellation. No engine compile/download after readiness.

51. Key TensorRT engine caches by compatible model/runtime/GPU/profile; warm advertised shapes before ready.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L09-1 | Cold-start a release container with empty engine cache. | Replica remains unready until engine build/warmup finishes; no customer request triggers compile. | Ready before warmup or first customer request compiles. |
| L09-2 | Restart from valid cache five times, then corrupt/change runtime/profile. | Valid cache starts predictably; incompatible cache rebuilds/unreadies safely. | Incompatible engine reused. |
| L09-3 | Run sustained unique W1/W2/W4/W7 traffic while capturing queue+GPU execution. | Stable p99 and windows/s curve; no CPU fallback or NaNs. | Throughput inferred from 1/p50 or hidden fallback. |
| L09-4 | Cancel queued/active requests. | Queue capacity/VRAM recover; cancelled work does not leak indefinitely. | Cancelled requests consume unbounded GPU queue/contexts. |

### Exit criteria

- Release-container parity passes.

- Actual GPU placement proven.

- q_safe windows/s and request-shape curves established.

- Readiness/cache/cancellation lifecycle proven.

### Evidence package

- runtime manifest

- model hashes

- parity report

- raw latency/throughput arrays

- ORT/TensorRT placement evidence

- cache/restart timelines

### Rollback / stop rule

Select the last validated local artifact/runtime. Never fall back to CPU or cloud silently while retaining the qualified latency badge.

**T10 Productionize output security and explicit streaming/strict modes**

| **Field** | **Value** |
|----|----|
| Phase | Local AI |
| Depends on | T01,T03,T04,T07,T09 |
| Primary owner | Streaming engineer + security/ML owner |
| Objective | Provide real output protection whose wire behavior matches the organization-selected action without silently destroying streaming. |

### Why this task exists

Output security is not “run PG2 again.” It may need sensitive-span detection, output taxonomy, grounding or other local capabilities, and strict blocking has different latency semantics from SSE.

### Implementation work

52. Finalize the output capability ledger and choose local implementations for each supported policy. Do not advertise unsupported Haiku-era categories as preserved.

53. Implement streaming mode with bounded lookbehind/coalescing and stateful boundary-safe redaction. Measure every upstream-receive → safe-client-write lag.

54. Implement strict whole-response mode as a separate option with bounded response size and zero prohibited content released before final verdict.

55. Keep completion-time observational checks labeled observational; they cannot retroactively block released bytes.

56. On detector timeout/crash, follow the organization’s selected supported failure posture while reporting degraded/unavailable truthfully.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L10-1 | Place synthetic secrets at every SSE chunk/UTF-8 boundary. | Streaming redaction prevents leakage and preserves valid SSE/JSON. | Split secret escapes or encoding breaks. |
| L10-2 | Strict BLOCK with harmful content at start/middle/end. | Client receives zero assistant content before terminal outcome. | Any blocked content appears first. |
| L10-3 | Streaming BLOCK after some safe chunks. | Behavior matches the signed truncation/terminal contract and is not labeled whole-response withholding. | Attempts to retroactively change HTTP 200 or claims zero-byte block. |
| L10-4 | Kill output detector before first content, mid-stream and at finalization. | Selected failure policy is applied consistently; unavailable scan not called benign. | Fail-open skip or inconsistent stream/non-stream behavior. |

### Exit criteria

- Every advertised output action is byte-proven.

- Streaming and strict modes have separate latency semantics.

- No output detector skip under load/failure.

- Output work is included in capacity accounting.

### Evidence package

- raw SSE captures

- client release timestamps

- output model/window counters

- capability/quality report

- strict-mode zero-byte proof

### Rollback / stop rule

Restore the last validated local output guard or disable the unsupported capability explicitly. Never revert to silent output fail-open.

**T11 Prove SSE protocol correctness under normal, slow, malformed and failed streams**

| **Field** | **Value** |
|----|----|
| Phase | Protocol |
| Depends on | T02,T03,T10 |
| Primary owner | Backend streaming engineer + edge QA |
| Objective | Make SSE a protocol contract rather than ad-hoc data strings. |

### Why this task exists

An OpenAI-compatible gateway must preserve incremental chunks, terminal semantics, backpressure and cancellation through proxies and security transforms.

### Implementation work

57. Preserve text/event-stream framing; each data event contains valid chunk JSON or the terminal \[DONE\] marker for Chat Completions.

58. Preserve chunk ordering, ids/model fields, role/content deltas, tool-call argument fragments, finish_reason and stream_options usage behavior where supported.

59. Ensure proxies do not accidentally buffer the entire response; use bounded coalescing only when part of the signed output-security mode.

60. After any client-visible bytes, never retry by appending a second provider response. Before any bytes, only retry/fallback under explicit safe routing rules.

61. Propagate client disconnect/cancel upstream and to guard work. Enforce backpressure with bounded buffers.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L11-1 | Synthetic provider emits 1-token chunks at fixed intervals. Capture raw socket/SSE client timings. | Frames arrive incrementally in order and terminate correctly. | Whole response arrives at once, merged/malformed frames, missing terminal. |
| L11-2 | Provider streams fragmented tool_calls/function arguments. | Official SDK reconstructs tool call correctly; gateway does not corrupt fragment order. | JSON/tool fragments altered or dropped. |
| L11-3 | Provider emits malformed SSE / clean EOF before completion. | Gateway surfaces a protocol/stream error according to contract; no fabricated success. | Malformed data becomes normal completion. |
| L11-4 | Disconnect provider before first byte vs after several chunks. | Pre-byte retry follows configured safe policy; post-byte path never splices a replacement answer. | Second provider content appended after partial stream. |
| L11-5 | Slow client consumes one event every 500 ms. | Memory remains bounded and backpressure propagates. | Gateway buffers response unboundedly. |
| L11-6 | Cancel client after N chunks. | Provider/guard work cancels and resource counters return to baseline. | Orphan generation continues indefinitely. |

### Exit criteria

- Wire-level SSE parser passes normal and failure cases.

- Bounded-buffer/backpressure and disconnect cleanup proven.

- No retry-after-release corruption.

- Release-lag metrics available for every emitted unit.

### Evidence package

- pcap/raw SSE transcripts

- producer/client timestamps

- buffer/FD/RSS curves

- disconnect cancellation trace

- tool-call stream capture

### Rollback / stop rule

Revert to last wire-compatible streaming implementation; do not disable output security to regain chunking.

**T12 Prove OpenAI Python and Node SDK compatibility against live staging**

| **Field** | **Value** |
|----|----|
| Phase | Protocol |
| Depends on | T11 |
| Primary owner | API compatibility engineer + QA |
| Objective | Guarantee that real clients can point their OpenAI SDK base URL at AI Mesh without custom parsing hacks. |

### Why this task exists

OpenAI-shaped JSON is insufficient. Official SDKs exercise request serialization, SSE parsing, tool-call accumulation, errors and cancellation behavior that curl misses.

### Implementation work

62. Pin supported OpenAI Python and Node SDK versions in the compatibility matrix and run them against the public staging base URL with AI Mesh API keys.

63. Cover synchronous and asynchronous clients, non-streaming Chat Completions, create(stream=True), higher-level chat completion stream helpers where available, and tool-call streaming.

64. Validate request headers, model names, usage, finish_reason, error object shape/status, timeouts, cancellation and custom base_url/baseURL behavior.

65. Add Responses API only if the product intentionally supports it; do not accidentally advertise compatibility because /v1/chat/completions works.

66. Repeat the SDK suite through the production-shaped edge and while selected security actions alter/block/redact the request.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L12-1 | Python OpenAI sync client: non-stream chat via base_url. | Parsed ChatCompletion object matches contract. | Custom wrapper required or fields malformed. |
| L12-2 | Python sync + AsyncOpenAI stream on safe text. | SDK iterates valid chunks and completes normally. | SDK parser errors, duplicate/missing chunks or hang. |
| L12-3 | Node OpenAI chat.completions.create({stream:true}). | Async iterator completes and reconstructs output. | Protocol incompatible with official Node SDK. |
| L12-4 | Python/Node tool call with fragmented arguments. | SDK surfaces one coherent tool call with correct finish_reason. | Fragments corrupted or reordered. |
| L12-5 | BLOCK/429/503/malformed-upstream cases. | SDK receives documented status/error; no false successful completion. | Gateway returns ad-hoc body the SDK misclassifies. |
| L12-6 | Client timeout/cancel during SSE. | SDK abort propagates; server/provider resources release. | Server keeps orphan work or emits success audit. |

### Exit criteria

- Official Python and Node SDKs pass the supported matrix without custom client code.

- Streaming/tool/error behaviors pass through the real edge.

- Supported/unsupported APIs are explicitly documented.

### Evidence package

- SDK versions/lockfiles

- live run logs

- request IDs

- tool stream transcripts

- error matrix

- edge endpoint/base URL proof

### Rollback / stop rule

Preserve the last SDK-compatible API schema. Server-side security changes must remain backward compatible or be versioned deliberately.

**T13 Make routing, retry, fallback, cancellation and provider errors stream-safe**

| **Field** | **Value** |
|----|----|
| Phase | Protocol |
| Depends on | T05,T06,T11,T12 |
| Primary owner | Routing/backend engineer |
| Objective | Keep deterministic routing and provider resilience without double generation, policy bypass or corrupt streams. |

### Why this task exists

Provider failures happen at different phases. A gateway must distinguish “nothing delivered yet” from “stream already visible.”

### Implementation work

67. Keep routing deterministic and policy-driven; no external AI adjudicator. Apply allowed-model/compliance/sensitivity constraints before dispatch.

68. Reuse pooled provider connections and explicit per-provider timeouts/circuit state; isolate organization/provider health where appropriate.

69. Before first downstream byte, allow only the signed safe retry/fallback policy. After bytes, terminate consistently—never splice another answer.

70. Carry idempotency/attempt IDs through audit and quota accounting; prevent duplicate authoritative events/provider calls from client retries.

71. Propagate client cancellation through provider and local guards; settle quota/leases according to the signed contract.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L13-1 | Primary provider 500 before first token with configured fallback. | Exactly one fallback attempt if allowed; no duplicate client chunks. | Multiple hidden retries or fallback ignores org model policy. |
| L13-2 | Primary fails after 3 chunks. | No second provider appended; stream terminates according to contract. | Mixed responses. |
| L13-3 | Two concurrent tenants have different allowed-model lists/fallback chains. | Routing remains tenant-specific under failure. | Global health state bypasses tenant policy. |
| L13-4 | Client retries same request/idempotency key. | Duplicate dispatch/audit behavior is explicit and bounded. | Unnoticed double generation/charge. |

### Exit criteria

- Deterministic routing only.

- Retry behavior depends on stream-delivery phase.

- No duplicate/mixed completion.

- Provider health/fallback remains tenant-policy compliant.

### Evidence package

- routing/fallback matrix

- provider recorder attempts

- SSE partial-failure captures

- quota/audit attempt joins

### Rollback / stop rule

Disable unsafe fallback paths before weakening policy or stream correctness.

**T14 Reduce auth/quota/kill-switch/shared-state latency without losing truth**

| **Field** | **Value** |
|----|----|
| Phase | Hot path |
| Depends on | T03,T05,T17 |
| Primary owner | Distributed systems/backend engineer |
| Objective | Keep authoritative multi-tenant control while removing serial network waits that destroy p99 and scale. |

### Why this task exists

Historical measurements showed shared-state RTTs could dominate the gateway even with fast models. Scaling gateway replicas is useless if one centralized path serializes every request.

### Implementation work

72. Profile auth, quota, kill-switch and model-state calls separately: pool wait, network RTT, server execution and retry time.

73. Cache/snapshot only data with explicit freshness semantics. Keep urgent revocation/kill-switch within a signed bound and reconcile missed updates.

74. Use local GCRA/token-bucket for burst/RPM where semantics permit; keep organization/global token quota authoritative through a shared lease/atomic operation so N replicas do not multiply allowance.

75. Pipeline only truly independent reads; do not reorder policy transformation and scanning dependencies.

76. Bound pool waits and retries. Overload becomes explicit 429/503 according to cause, not a growing queue.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L14-1 | Two gateway replicas hammer the same org quota. | Combined admits never exceed signed global quota beyond defined lease tolerance. | Each replica independently grants full quota. |
| L14-2 | Flip kill switch while steady traffic runs. | All replicas stop within signed freshness bound. | Stale replica continues indefinitely. |
| L14-3 | Drop pub/sub/notification channel. | Periodic reconcile restores correct state within bound. | Only live notifications maintain correctness. |
| L14-4 | Exhaust Redis pool / inject 50–200 ms latency. | Waits remain bounded and failure outcome is explicit; event loop remains responsive. | Unbounded queue/p99 collapse or fail-open. |

### Exit criteria

- Steady-state shared-state operations and freshness contract documented.

- Global quota remains global across replicas.

- Kill switch and auth revocation bounds proven.

- Pool waits/retries bounded inside latency budget.

### Evidence package

- shared-state call trace

- quota multi-replica report

- kill-switch timeline

- pool saturation curves

- error-code matrix

### Rollback / stop rule

Restore the last safe authoritative state path and reduce traffic. Never trade global quota/kill-switch correctness for a faster chart.

**T15 Move telemetry and analytics off the synchronous request path**

| **Field** | **Value** |
|----|----|
| Phase | Hot path |
| Depends on | T03,T02 |
| Primary owner | Observability/backend + control-plane engineer |
| Objective | Ensure logs, dashboards and analytics cannot add unpredictable chat latency or become a fleet-wide failure dependency. |

### Why this task exists

Synchronous Redis/DB logging and heavy dashboard queries turn observability into a data-plane dependency. Industry gateways keep this work asynchronous.

### Implementation work

77. Replace synchronous request-path log publishing with a bounded async producer; define retry/spill/loss accounting separately from enqueue success.

78. Persist one compact authoritative decision event plus content references rather than repeating full payloads across stages.

79. Keep high-cardinality request data out of metrics labels. Cap metric cardinality and export fleet histograms/counters only.

80. Push dashboard aggregates to bounded SQL/rollups and abort/debounce superseded browser requests. No raw unbounded ORM scans on co-located serving resources.

81. Define what happens if audit durability is required by a particular compliance profile; do not silently fail chat or silently lose audit.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L15-1 | Add 500 ms latency/unavailability to audit sink under gateway load. | Gateway p99 changes within signed tolerance; queue/spill/loss is visible. | Chat event loop blocks on logging or memory grows unbounded. |
| L15-2 | Fill async audit buffer to limit. | Signed overflow behavior occurs predictably. | Unbounded memory or silent drop. |
| L15-3 | Open heavy dashboard analytics while gateway runs at 70–90% q_safe. | Gateway p99/resource profile remains within approved interference budget. | Dashboard query destabilizes serving fleet. |
| L15-4 | Reload Scan Detail after async persistence delay. | UI shows pending/unavailable truthfully then resolves; no fabricated data. | UI claims durable success before persistence or exposes raw secret. |

### Exit criteria

- No synchronous analytics/log sink on hot path.

- Bounded async queues and loss accounting.

- Dashboard workload does not invalidate qualified gateway profile.

- Audit/UI request-ID correlation preserved.

### Evidence package

- publisher call audit

- sink-fault latency comparison

- queue/backlog charts

- dashboard contention run

- durable-ID reconciliation

### Rollback / stop rule

Use version-compatible event readers/writers. Never restore raw secret copies or synchronous telemetry as a quick fix.

**T16 Harden the public edge, connection reuse, timeouts and long-lived SSE**

| **Field** | **Value** |
|----|----|
| Phase | Hot path |
| Depends on | T02,T11 |
| Primary owner | Edge/SRE engineer |
| Objective | Make the network edge transparent to streaming, high connection counts, graceful drains and security requirements. |

### Why this task exists

Even correct application streaming fails if nginx/LB buffers, closes idle streams, churns backend connections or routes unknown hosts incorrectly.

### Implementation work

82. Pin the real edge chain and keep chat/SDK traffic separate from the operator SPA/control edge where practical.

83. Configure upstream keepalive/connection reuse, SSE proxy buffering off, appropriate gzip policy, idle/max-stream timeouts and graceful connection drain.

84. Verify client HTTP/2 where supported and actual internal protocol behavior rather than assuming every hop is H2.

85. Preserve security headers/host isolation without origin redirect loops behind TLS termination.

86. Record connection/fd/SNAT behavior and capacity at load; edge must not be the hidden limiter before application capacity.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L16-1 | Stream fixed-interval SSE through the full public staging edge with gzip identity/on/off as supported. | Incremental release remains correct; no full buffering. | Proxy batches/holds full response or corrupts stream. |
| L16-2 | Run a controlled \>350-second healthy stream. | Stays alive under configured timeouts and ends cleanly. | Unexpected LB/proxy timeout. |
| L16-3 | Drain/restart one gateway replica during active streams. | New traffic avoids unready replica; active streams follow signed drain behavior. | Connections reset indiscriminately or new requests hit unready node. |
| L16-4 | High connection/stream count with keepalive. | FD/connection reuse behaves as designed; no SNAT/connect-timeout collapse. | Backend opens one new TCP per request or edge saturates early. |
| L16-5 | Unknown Host and security-header matrix. | Unknown host rejected; intended host works with required headers. | SPA/control served on unintended host or security config breaks SDK/SSE. |

### Exit criteria

- SSE passes through real edge incrementally.

- Long-stream and graceful-drain behavior proven.

- Connection reuse/FD limits measured.

- Unknown-host/security-header posture retained.

### Evidence package

- raw edge SSE timing

- connection/FD metrics

- long-stream log

- drain timeline

- header/host matrix

### Rollback / stop rule

Restore known-safe edge image/config through change control; never disable auth/host isolation or output security to recover health checks.

**T17 Implement resource-aware admission, bounded queues and backpressure**

| **Field** | **Value** |
|----|----|
| Phase | Hot path |
| Depends on | T02,T09,T11,T14 |
| Primary owner | Backend + runtime + SRE |
| Objective | Fail predictably before saturation turns into seconds of latency, OOM or random 502s. |

### Why this task exists

A scalable gateway must bound concurrent memory/FD/GPU work yet derive limits from actual resources and service rates rather than today’s topology.

### Implementation work

87. Derive worker count, request concurrency, queue capacity, FDs and memory budgets from cgroups/limits and measured per-request resource cost; deployment overrides remain explicit.

88. Implement bounded gateway and per-tenant admission queues; local guard queue and SSE buffers are separately bounded. Expose queue depth/age.

89. Use deadlines based on the signed p99 budget. Reject/shed overload quickly with explicit codes and Retry-After where appropriate rather than increasing timeouts.

90. Propagate backpressure from slow client/output processing to upstream/provider reads. Cancellation removes queued/in-flight local work promptly.

91. Scope circuit breakers so one tenant/direction fault does not degrade unrelated tenants unless the dependency is truly global.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L17-1 | Offer 3× known safe rate for a bounded interval. | System sheds/rejects explicitly; p99 of admitted cohort stays bounded; no OOM. | Queues grow without bound or random reset/502 storm. |
| L17-2 | Slow 10% of downstream clients heavily. | Only their streams consume bounded buffers/backpressure; fleet memory plateaus. | Slow clients cause fleet-wide memory growth. |
| L17-3 | Fill guard queue while CPU gateway remains healthy. | Admission/guard capacity error is explicit; no scan skip. | Gateway keeps accepting then silently bypasses semantic guard. |
| L17-4 | Tenant A causes breaker/overload condition. | Tenant B healthy traffic remains within contract unless shared global dependency truly failed. | Cross-tenant breaker contamination. |

### Exit criteria

- Every queue/pool has a bound, metric and overload behavior.

- No OOM/resource leak at 3× offered load.

- Admitted cohort retains bounded latency.

- Backpressure/cancellation frees resources.

### Evidence package

- resource-budget calculation

- 3× overload curves

- queue-age histograms

- RSS/FD/VRAM plateau

- tenant-isolation fault results

### Rollback / stop rule

Lower admission and traffic rather than widening queues/timeouts. Never disable guards to regain throughput.

**T18 Measure one serving unit: p99, RPS/vCPU, RPS/L4 and safe saturation point**

| **Field** | **Value** |
|----|----|
| Phase | Performance |
| Depends on | T03,T07,T09,T10,T14,T15,T16,T17 |
| Primary owner | Performance engineer + independent reviewer |
| Objective | Find the real service-rate limits before sizing the \$5k fleet. |

### Why this task exists

The useful questions are how much CPU work each request consumes, how many guarded requests each L4 can sustain under the p99 budget, and where queues begin to dominate.

### Implementation work

92. Freeze images, model artifacts, policy profile, input/output distributions, cache state and output mode. Use unique prompts and a controlled token-emitting provider.

93. Sweep guard window count/batch/concurrency first to obtain q_safe windows/s and request-shape curves under the complete latency budget.

94. Sweep gateway CPU allocations (where feasible 1/2/4/8 vCPU) and worker/event-loop settings without changing semantics. Record actual cgroup CPU usage/throttling.

95. Use open-loop arrival-rate steps until the first repeatable p99/error/resource failure. Record both highest passing and adjacent failing rate.

96. Run profile/instrumentation separately from final measurements. Do not average per-worker p99; merge raw samples/histograms.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L18-1 | 1 vCPU equivalent gateway sweep with adequate guard capacity. | Measured qualified RPS/vCPU and CPU ms/request; no hidden CPU outside denominator. | Nominal vCPU claim based on host nproc or excludes serving sidecar CPU. |
| L18-2 | Single L4 unique-request sweep across W1/W2/W4/W7 mix. | Highest passing RPS/L4 under p99\<20 ms recorded. | Uses 1/p50, repeated cache hits or omits output work. |
| L18-3 | Increase offered rate across at least 5 steps to failure. | Clear knee/queue growth and adjacent FAIL point retained. | Only best burst published. |
| L18-4 | Repeat highest passing point 3× after restart/warmup. | Results repeat within signed variance. | One lucky run or warm-cache artifact. |

### Exit criteria

- q_safe per L4 and per-node rate known.

- RPS/vCPU and CPU ms/request measured honestly.

- Highest passing and first failing rate documented.

- No hidden loadgen bottleneck/schedule drops.

### Evidence package

- raw JSONL/histograms

- CPU/GPU/queue metrics

- rate sweep curves

- restart repeats

- loadgen headroom proof

### Rollback / stop rule

Restore validated queue/batch/worker settings. Do not retune quality thresholds or disable output checks to rescue a rate point.

**T19 Prove horizontal scaling with no application-code changes**

| **Field** | **Value** |
|----|----|
| Phase | Performance |
| Depends on | T18,T14,T17 |
| Primary owner | SRE + performance engineer |
| Objective | Show that added replicas/GPUs increase capacity without rewriting logic or changing tenant semantics. |

### Why this task exists

This is the core future-proofing requirement. A codebase that only scales after changing queue code, global locks or policy logic is not horizontally scalable.

### Implementation work

97. Run the same immutable application image/config contract at 1,2 and 4 gateway serving units with proportionate guard capacity. Only deployment replica/resource values change.

98. Keep shared state, policy versioning and tenant quotas identical. Verify no centralized Redis/control/audit bottleneck flattens the curve.

99. Use the same open-loop workload and calculate scale efficiency = observed throughput / (single-unit throughput × unit count).

100. Measure p99, queue age, Redis/shared-state utilization, network/edge connections and cost at each scale point.

101. If scaling flattens, identify the next binding component and fix that interface rather than adding code paths per topology.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L19-1 | Run 1 serving unit at 80–90% q_safe. | Baseline stable and reproducible. | Single-unit baseline not stable. |
| L19-2 | Run 2 units with same image and policy; double offered load progressively. | Capacity rises materially (target ≥1.7× before next bottleneck) without p99/security regression. | \<1.5× or new correctness divergence without identified external bottleneck. |
| L19-3 | Run 4 units similarly. | Near-linear scale continues until a named shared bottleneck; no code change. | Requires application rewrite/per-node policy differences to scale. |
| L19-4 | During scale test, flip one org policy and kill one replica. | All ready replicas converge; traffic drains/rebalances. | Stale config or single-replica state loss affects correctness. |

### Exit criteria

- Same application image scales 1→2→4.

- Scaling efficiency curve and next bottleneck documented.

- No tenant/quota multiplication or cross-talk.

- Code contains no topology-specific capacity assumption.

### Evidence package

- 1/2/4-unit manifests

- throughput/p99 scaling curves

- shared-state saturation metrics

- policy convergence timeline

- replica-failure capture

### Rollback / stop rule

Scale back replicas via deployment controls. No code fork should exist solely for a smaller fleet.

**T20 Optimize and qualify the ≤\$5k production envelope**

| **Field** | **Value** |
|----|----|
| Phase | Performance |
| Depends on | T18,T19 |
| Primary owner | Infrastructure owner + performance engineer + cost approver |
| Objective | Choose the fastest fully qualified topology that fits today’s budget while keeping future scale-out code unchanged. |

### Why this task exists

The budget is a deployment constraint. We need to spend it on the binding resource, not copy an old 4-node shape after software/runtime improvements change the bottleneck.

### Implementation work

102. Reprice current candidate cloud shapes, egress, LB, shared state, storage, monitoring and support. Keep customer model-token spend separate.

103. Use T18/T19 measured q_safe, CPU demand and wire bytes to compare deployment shapes. Reuse spare CPU on GPU nodes only if control-plane interference tests remain green.

104. Maximize qualified RPS under ≤\$5,000/month with declared HA/reserve assumptions. Historical 4×G2/8×L4/~\$4,561 is the baseline to re-evaluate, not a fixed topology.

105. Compute qualified RPS/\$1,000-month, RPS/L4, purchased-vCPU utilization, cost/million requests and headroom.

106. Retain at least one safe rollback topology/image; budget cannot be “met” by deleting required monitoring/audit/security controls.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L20-1 | Deploy the chosen budget topology in production-like staging and run the highest qualified plateau. | All required controls + p99 target pass within verified monthly cost estimate. | Budget only passes by excluding required egress/control/audit or skipping guards. |
| L20-2 | Run control/dashboard activity concurrently. | Co-located services do not push gateway outside p99/resource limits. | “Free spare CPU” assumption invalid. |
| L20-3 | Recompute bill from measured bytes/RPS and dated vendor prices independently. | Second calculation agrees within signed tolerance and remains ≤\$5k. | Old cost table copied despite different traffic/price. |
| L20-4 | Increase rate above qualified point without changing resources. | First repeatable SLO miss identifies the real ceiling. | Advertised capacity based on one-second burst. |

### Exit criteria

- Verified ≤\$5k monthly envelope.

- Maximum qualified RPS under that envelope measured.

- Cost per million and bottleneck stated.

- No application logic depends on the chosen node/GPU count.

### Evidence package

- dated pricing/cost workbook

- measured wire-byte model

- topology manifest

- qualification run

- above-ceiling failure point

### Rollback / stop rule

Scale back to last qualified budget topology/image. Never reduce security coverage to keep a budget claim.

**T21 Run resilience, chaos and rollback drills under load**

| **Field** | **Value** |
|----|----|
| Phase | Resilience |
| Depends on | T17,T19,T20 |
| Primary owner | SRE + security QA |
| Objective | Prove that component failures become bounded, truthful behavior rather than fleet-wide corruption or silent security bypass. |

### Why this task exists

Industry-grade gateways must survive or shed cleanly when replicas, caches, providers, local models and telemetry fail.

### Implementation work

107. Approve a staging-only fault matrix with abort thresholds and named recovery operator.

108. Inject one fault at a time at 60–80% qualified load, then selected combined faults after single-fault behavior is stable.

109. Measure customer-visible status, policy action, leakage/provider dispatch, queue recovery, readiness, MTTR and cross-tenant blast radius.

110. Rehearse immutable rollback with connection drain, compatible config/schema and local-only guard availability.

111. Keep availability-under-failure separate from security-under-failure; a safe 503 is not full-rate success but is better than unsafe allow.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L21-1 | Kill one gateway replica during SSE load. | New traffic shifts; active streams follow drain/termination contract; no tenant state loss. | Fleet outage or stale local-only state required. |
| L21-2 | Kill one local guard replica/GPU or corrupt engine cache. | Unready route removed; required checks follow failure policy; no external fallback. | Scan skip/benign result. |
| L21-3 | Fail/restart Redis/Valkey primary or block connectivity. | Auth/quota/KS follow signed fail behavior and recover boundedly. | Quota duplication, stale kill switch, indefinite hang. |
| L21-4 | Blackhole audit/analytics sink. | Serving path remains within contract or signed audit-fail policy; bounded queue. | OOM or unaccounted silent loss. |
| L21-5 | Slow/blackhole provider. | Circuit/timeouts bounded; pre-byte fallback only as allowed; no post-byte splice. | Queue collapse or mixed answer. |
| L21-6 | Rollback candidate to previous image under live staging traffic. | Known-safe version becomes ready and functional; old external AI path is not resurrected. | Schema/config mismatch or unsafe fallback. |

### Exit criteria

- Every declared fault has documented safe behavior and bounded recovery.

- Cross-tenant blast radius understood and within contract.

- Rollback actually works with live streams.

- No cloud-AI fallback appears during failure.

### Evidence package

- fault-matrix.json

- request/byte captures

- resource/recovery curves

- MTTR table

- rollback drill log

### Rollback / stop rule

Abort load/fault injection immediately on safety invariant breach. Restore last known secure deployment and preserve failed evidence.

**T22 Prove redesigned frontend control and tenant actions under concurrent load**

| **Field** | **Value** |
|----|----|
| Phase | Resilience |
| Depends on | T05,T06,T10,T12,T20 |
| Primary owner | Frontend engineer + browser QA + backend owner |
| Objective | Prove that the organization truly controls supported actions through the real product UI and that the UI remains truthful under load/failure. |

### Why this task exists

A polished interface is still unsafe if it misrepresents backend execution. The redesigned console must reconcile user intent, policy version, detector execution, provider bytes, audit data and displayed outcome while remaining usable under concurrent load.

### Implementation work

112. Use the UI12-qualified redesigned production frontend against the production-like staging backend - no mocked network responses, dev-server-only proof or legacy UI fallback.

113. For each supported detector/action, persist config, reload, capture compiled policy version, send the same fixture and join frontend/request ID to provider/audit evidence.

114. Display final transport action, findings, mode, flag/review, detector executed/skipped/unavailable state, policy version and timing distinctly.

115. Exercise policy updates while backend load is active and ensure changes apply within freshness bound without cross-tenant contamination.

116. Keep browser analytics demand bounded so the UI cannot destabilize the serving fleet.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L22-1 | Two tenants open consoles and set opposite actions; send identical fixture simultaneously. | Each UI reflects its own backend/provider outcome and policy version. | Cross-tenant control/state leakage. |
| L22-2 | Cycle OFF→MONITOR→ENFORCE for input and output detectors while 70% load runs. | Behavior changes without restarting gateway; p99/freshness stay within limits. | Setting saves but stale workers or hidden floor remains. |
| L22-3 | Trigger 403 policy, 429 quota, 503 degraded, partial SSE and redaction. | UI distinguishes each; no “success” on infrastructure failure. | Errors collapsed into generic allow/fail badge. |
| L22-4 | Reload Scan Detail after event persistence and policy changes. | Same request keeps immutable decision versions; no raw canary exposed. | UI recomputes result from current policy or leaks secret. |

### Exit criteria

- Organization controls are demonstrably authoritative.

- Frontend/backend/provider/audit agree per request.

- Concurrent tenants isolated.

- UI remains truthful under load and degraded states.

### Evidence package

- Playwright traces/screenshots

- sanitized network captures

- policy-version timeline

- provider/audit joins

- two-tenant concurrency proof

### Rollback / stop rule

Frontend may roll back independently only if backend schema remains compatible. Never disable enforcement because an older UI cannot render a state.

**T23 Run final staging qualification: repeated plateaus, soak and cost reconciliation**

| **Field** | **Value** |
|----|----|
| Phase | Release |
| Depends on | T04,T12,T20,T21,T22 |
| Primary owner | Performance QA + independent verifier |
| Objective | Produce the single source of truth for the release claim under the current \$5k envelope. |

### Why this task exists

The release must pass correctness, protocol, security, cost and performance together, not in isolated demonstrations.

### Implementation work

117. Freeze candidate images, policy/model/tokenizer/corpus hashes, edge config, topology and acceptance contract.

118. Prove load generators and synthetic provider can exceed the intended offered rate without schedule drops or resource saturation.

119. Run a rate ladder to determine the candidate plateau, then three independent 30-minute qualification runs after warmup/restart. Use unique prompts and the full low-latency security profile.

120. Run a 2-hour soak at the highest qualified rate. Include representative input/output length strata and the signed stream/JSON mix.

121. Run negative-control qualification with one required guard deliberately disabled in isolated staging; harness must FAIL even if RPS improves.

122. Recompute cost/million and monthly cost from actual bytes and measured qualified RPS. Independent reviewer recomputes p99 and success counts from raw evidence.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L23-1 | Three 30-minute all-permitted plateaus at candidate max qualified rate. | Every run: p99 T_fw_addon \<20 ms, error ceiling met, zero safety breaches, zero schedule drops. | Any trial fails required stratum or guard coverage. |
| L23-2 | Worst-band prompt/output profile. | Profile-specific p99 and quality pass or is explicitly unqualified. | Mostly-short mix hides long-band failure. |
| L23-3 | 2-hour soak. | RSS/VRAM/FD/queue metrics plateau; no drift/leak/restart storm. | Monotonic growth or degradation. |
| L23-4 | Disable one required output/input guard in isolated staging. | Harness marks run ineligible/FAIL regardless of HTTP 200 rate. | Faster scanless run passes. |
| L23-5 | Independent raw-data recomputation. | Matches published percentiles/counts/cost denominator. | Dashboard aggregation cannot be reproduced. |

### Exit criteria

- Highest qualified RPS under ≤\$5k established.

- p99 \<20 ms passes every required run/stratum.

- OpenAI SDK/SSE and user-control gates remain green under load.

- Independent reviewer reproduces metrics.

### Evidence package

- frozen trial manifests

- raw JSONL/HDR histograms

- soak curves

- eligibility joins

- cost reconciliation

- independent recomputation log

### Rollback / stop rule

If any required gate fails, publish measured limit/bottleneck and keep last qualified rate. Never relax security thresholds or metric definitions after seeing results.

**T24 Canary with an authorized real provider and customer-shaped traffic**

| **Field** | **Value** |
|----|----|
| Phase | Release |
| Depends on | T23 |
| Primary owner | Release lead + SRE + product/security owner |
| Objective | Verify real BYOK/provider behavior without confusing provider quota with gateway capacity. |

### Why this task exists

Controlled providers are required for causal latency/capacity proof, but actual provider integrations can reveal SDK, streaming, timeout and policy interactions that staging generators cannot.

### Implementation work

123. Choose one authorized provider/account/model with documented quota and a synthetic/non-sensitive canary tenant. Keep rate well below provider quota initially.

124. Run non-stream and SSE via official OpenAI SDKs through AI Mesh with selected policies, tool calls if applicable, cancellation and error cases.

125. Compare direct-provider and through-AI-Mesh client timings, while treating provider intrinsic timing as uncertain unless the provider exposes it.

126. Advance canary traffic only after zero safety/compatibility regressions and acceptable tails. Keep customer/provider spend visible but outside firewall-infrastructure budget.

127. Record the exact tested provider throughput; do not extrapolate it to gateway capacity or another tenant.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L24-1 | Official Python/Node SDK against real provider through AI Mesh. | Same supported SDK contract passes as staging. | Provider-specific stream/tool shape breaks gateway. |
| L24-2 | Direct vs gateway request sample. | Gateway behaves correctly; latency difference labeled carefully without false causal subtraction. | Provider TTFT presented as firewall tax. |
| L24-3 | Provider rate-limit/error/cancel cases. | AI Mesh surfaces consistent typed errors and cancels work. | Hidden retries/splices or generic success. |
| L24-4 | Small canary policy changes by org admin. | Runtime behavior follows selected plan with no cross-tenant effect. | Canary requires code/config fork. |

### Exit criteria

- Real-provider SDK/stream compatibility passes.

- No external platform guard calls.

- Observed provider-supported rate/latency stated separately.

- Canary rollback path proven.

### Evidence package

- provider/account test manifest

- SDK logs

- direct-vs-gateway timing sample

- provider error matrix

- canary policy/action evidence

### Rollback / stop rule

Return tenant to last qualified release/provider route. Do not disable mandatory controls to work around a provider incompatibility.

**T25 Remove legacy Bedrock/Vertex/Gemini paths, credentials and regression escape hatches**

| **Field** | **Value** |
|----|----|
| Phase | Release |
| Depends on | T08,T21,T24 |
| Primary owner | Backend + DevOps + security owner |
| Objective | Make local-only enforcement permanent and prevent future code/config from silently re-enabling external AI. |

### Why this task exists

Final removal must happen after replacements and rollback are proven; deleting credentials first can produce hidden fail-open behavior or break unrelated AWS services.

### Implementation work

128. Re-audit reachable platform AI call sites and remove obsolete Tier-2 clients/factories, legacy provider env vars, scheduled jobs and control-plane security-engine callers.

129. Preserve unrelated SDK consumers (for example storage) rather than deleting broad dependencies by string match.

130. Build local-only images and run the complete live scenario suite with platform cloud-AI credentials absent and egress denied.

131. Only then remove narrowly scoped secrets/IAM/endpoint access through approved infrastructure change control. Rotate any exposed/retired credential as required.

132. Add CI/static/runtime regression gates that fail if forbidden external-AI hosts/clients become reachable from enforcement code again.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L25-1 | Run all staging surfaces with zero Bedrock/Google AI credentials and denied egress. | Supported features pass; zero forbidden attempts. | AccessDenied/connection attempts or silent allow. |
| L25-2 | Exercise failure/background/startup paths after code cleanup. | No hidden legacy fallback. | Only errors reveal remaining dependency. |
| L25-3 | Run unrelated AWS/storage/customer-provider features that should remain. | Still functional. | Over-broad cleanup breaks valid services. |
| L25-4 | Inject a forbidden external-AI client/hostname in a test branch/config. | Regression gate blocks build/deploy. | Forbidden dependency can return unnoticed. |

### Exit criteria

- No reachable platform external-AI enforcement path.

- Credentials removed only after callers.

- Unrelated services preserved.

- Regression gates prevent reintroduction.

### Evidence package

- final callgraph/static report

- zero-credential/egress live test

- IAM/secret change record

- preserved-service test

- regression-gate proof

### Rollback / stop rule

Use the last validated local-only image. Permission restoration is separate change control and must not revive cloud-AI enforcement implicitly.

**T26 Publish reproducible evidence, capacity limits and scale-up handoff**

| **Field** | **Value** |
|----|----|
| Phase | Release |
| Depends on | T23,T24,T25 |
| Primary owner | Independent verifier + release owner |
| Objective | Turn the release into a repeatable engineering artifact and make future budget increases deployment changes—not application rewrites. |

### Why this task exists

The final deliverable must state what is measured, what is not, and exactly how to add capacity without changing security semantics.

### Implementation work

133. Package frozen manifests, raw qualification data, image/model/policy hashes, cost workbook, SDK/SSE compatibility, chaos results and reviewer sign-off.

134. Publish the measured max qualified RPS under the current \$5k envelope, p50/p95/p99, RPS/vCPU, RPS/L4, CPU ms/request, cost/million and first failing bottleneck.

135. Document scale-up procedure: increase gateway replicas/CPU, guard replicas/GPUs, shared-state capacity and provider capacity; no application code changes expected unless a new bottleneck violates the existing service contract.

136. Document autoscaling signals and safe bounds for gateway, guard and provider fleets; keep queue/buffer semantics invariant.

137. Update architecture/Eraser sources to reflect actual process ownership and output mode. Clearly label unqualified profiles and real-provider limits.

138. Require an independent engineer to rebuild staging and reproduce a representative qualification slice from the bundle.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| L26-1 | Independent rebuild from release manifest on a clean environment. | Same images/models/config and core live tests pass. | Requires undocumented setup/manual patches. |
| L26-2 | Increase staging replicas/resources only and rerun a scaled load slice. | Capacity rises according to documented scaling path; no code change. | Scale-up requires changing application semantics. |
| L26-3 | Recompute published percentiles/cost from raw files. | Matches release report. | Only dashboard screenshots support claims. |

### Exit criteria

- Reproducibility bundle complete.

- Measured limits and exclusions explicit.

- Scale-up runbook demonstrates deployment-only growth path.

- Independent verifier signs the release evidence.

### Evidence package

- release-evidence.json

- raw data/archive hashes

- operations/scaling runbook

- architecture source/export

- independent replay report

- signed limits/exclusions

### Rollback / stop rule

Withhold publication if evidence is incomplete. Keep last qualified capacity limit and failed results as engineering evidence.

# 5. Cross-task live scenario catalog

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p><strong>Use these scenarios as release artifacts, not demos</strong></p>
<p>Each scenario runs against immutable Docker/staging images and produces request-ID-correlated wire, provider, policy, model, audit and resource evidence. A screenshot or unit assertion alone never closes a scenario.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **ID** | **Scenario** | **Live setup** | **Pass condition** |
|----|----|----|----|
| S01 | ALLOW baseline | Benign input, required guards healthy, SSE and JSON. | Exactly one provider dispatch; clean completion; all required checks executed. |
| S02 | Input BLOCK | Injection/credential fixture with organization action BLOCK. | Zero provider dispatch; single decision/audit; SDK receives documented error. |
| S03 | Input REDACT | PII fixture with REDACT. | Recorder sees sanitized payload; raw value absent from provider/log/UI errors. |
| S04 | MONITOR | Injection rule in MONITOR. | Finding recorded; configured non-enforcing transport outcome; other mandatory controls still apply. |
| S05 | FLAG | Output/input finding with FLAG. | Traffic outcome and flag/review state separately visible. |
| S06 | Rule OFF | Disable one optional detector. | No call/work/effect solely for that detector; plan version changes. |
| S07 | Conflicting rules | PII redact + independent credential block. | Block wins by signed rule semantics; findings preserved. |
| S08 | Missing org config | Known tenant with missing/unloaded plan. | Not-ready/deny per contract; never another tenant/default. |
| S09 | Local guard unavailable | Kill guard service/GPU. | Selected failure posture; no benign fabrication or cloud fallback. |
| S10 | Long-input windows | Attacks in first/middle/last window. | All covered or explicit length reject; no silent truncation. |
| S11 | SSE normal | Fixed-rate safe chunks. | Incremental valid chunks + terminal marker; release lag measured. |
| S12 | SSE boundary redaction | Secret split across chunks/UTF-8 boundary. | No leak; valid protocol. |
| S13 | Strict output BLOCK | Unsafe whole response. | Zero prohibited assistant bytes before terminal result. |
| S14 | Malformed upstream SSE | Broken event/json/clean EOF. | Protocol error/degraded outcome; not success. |
| S15 | Provider fail pre-byte | Primary fails before first content. | Only signed safe retry/fallback executes. |
| S16 | Provider fail post-byte | Primary fails after partial stream. | No fallback splice; clean termination semantics. |
| S17 | Client cancel | Cancel after N chunks. | Provider/guard work stops; resources return to baseline. |
| S18 | Slow consumer | Very slow SDK client. | Bounded buffers; backpressure; no fleet memory growth. |
| S19 | OpenAI Python sync | base_url + chat completion. | SDK returns typed completion. |
| S20 | OpenAI Python async stream | AsyncOpenAI streaming. | Iterator completes; chunks valid. |
| S21 | OpenAI Node stream | Node async iterator. | Chunks parse/complete without custom client code. |
| S22 | Tool-call stream | Fragmented tool args. | SDK reconstructs exact tool call. |
| S23 | Usage/finish reason | Streaming usage + finish_reason. | Fields arrive according to supported contract. |
| S24 | Two-tenant isolation | Opposite rules under concurrency. | No config/finding/action leakage. |
| S25 | Policy update under load | Change action at 70% load. | All ready replicas converge within freshness bound. |
| S26 | Redis/Valkey fault | Latency/failover/disconnect. | Bounded safe behavior; no quota multiplication. |
| S27 | Audit sink fault | Slow/down/full queue. | Bounded queue/spill/visible loss; gateway stable. |
| S28 | Gateway kill/drain | Terminate one replica. | Traffic rebalances; expected stream behavior. |
| S29 | Guard kill/cache corruption | Kill local model or corrupt engine cache. | Unready/fail policy; no cloud fallback. |
| S30 | Overload 3× | Offer 3× q_safe. | Explicit shedding; no OOM/unbounded queues. |
| S31 | Single-unit rate sweep | Open-loop increasing RPS. | Highest passing + first failing point retained. |
| S32 | 1→2→4 scale | Same app image, more replicas/capacity. | Material capacity growth without code changes. |
| S33 | Dashboard contention | Heavy console analytics during load. | Serving p99/resource budget remains valid. |
| S34 | 30-minute qualification | Frozen candidate plateau. | p99\<20ms + error/security gates in every run. |
| S35 | 2-hour soak | Highest qualified rate. | Resources plateau; no drift/restart storm. |
| S36 | Zero external AI | Deny Bedrock/Google AI egress. | Zero forbidden attempts across all surfaces/failures. |
| S37 | Real-provider canary | Official SDK through authorized BYOK. | Compatibility passes at tested provider quota. |
| S38 | Rollback drill | Redeploy prior local-only image. | Ready/healthy, core actions preserved, no external AI regression. |
| S39 | Cost recomputation | Rebuild monthly cost from measured bytes/resources. | ≤\$5k with declared exclusions/headroom. |
| S40 | Scale-up rehearsal | Add replicas/GPUs only. | Capacity increases; application code unchanged. |

# 6. Final release gate

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p><strong>Release PASS requires all rows</strong></p>
<p>A single failed safety/protocol/user-control gate blocks release even if the performance chart is excellent. A performance miss does not authorize weakening enabled controls.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Gate** | **PASS requirement** |
|----|----|
| Identity | Candidate commit, images, frontend, policy and model hashes frozen and reproducible. |
| Local-only enforcement | Zero platform-owned Bedrock/Vertex/Gemini/hosted-AI attempts in normal and failure paths. |
| User authority | Organization settings change actual detector execution/action; OFF/MONITOR/ENFORCE semantics byte-proven. |
| Input safety | BLOCK → zero provider calls; REDACT → sanitized provider bytes; no hidden rule floors beyond signed platform integrity. |
| Output safety | Streaming/strict semantics match selected mode; no secret escapes chunk boundaries; failures truthful. |
| SSE | Official SDKs parse incremental chunks/tools/errors; no accidental buffering/spliced retry; \[DONE\]/terminal behavior correct. |
| OpenAI SDK | Pinned Python + Node SDK matrix passes through real edge. |
| Latency | p99 complete firewall overhead \<20 ms for every required low-latency stratum/trial. |
| Throughput | Maximum qualified RPS under ≤\$5k measured with unique, fully guarded requests; historical 1,064 floor beaten or gap explicitly reported. |
| Efficiency | RPS/vCPU, CPU ms/request, RPS/L4, queue time and cost/million measured; denominator complete. |
| Scale | 1→2→4 serving units increase capacity materially with same application image/semantics. |
| Resilience | Fault matrix and rollback pass with no cross-tenant or fail-open security violation. |
| Soak | Three independent 30-minute plateaus + 2-hour soak pass; zero loadgen schedule drops at qualified plateau. |
| Budget | Current dated infrastructure estimate ≤\$5,000/month with prompt egress, LB, state, audit/monitoring and required support assumptions declared. |
| Evidence | Independent reviewer recomputes raw percentiles/counts/cost and rebuilds representative staging proof. |

## 6.1 Stop conditions

- Any raw sensitive canary reaches a provider/client when selected policy required redaction/block.

- Any tenant receives another tenant’s policy, content, request ID or audit record.

- Any required local semantic check becomes a benign result merely because the model/service was unavailable.

- Any performance run hides queueing, provider TTFT, release holdback, schedule drops or failed/cancelled requests.

- Any external platform AI call appears after the local-only gate.

- Any queue/buffer grows without a configured bound or causes OOM/FD exhaustion.

- Any official SDK streaming test hangs, corrupts events or requires a private parser workaround.

- Any deployment meets the budget only by excluding required infrastructure or security work.

# 7. How future budget increases should scale the system

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th>TODAY ($5k envelope)<br />
same gateway image + same policy schema + same guard API<br />
<br />
BUDGET INCREASE<br />
├─ add gateway replicas / CPU<br />
├─ add local guard replicas / GPUs<br />
├─ scale authoritative shared-state capacity<br />
├─ scale edge/load-balancer targets<br />
└─ obtain more customer/dedicated provider capacity<br />
<br />
EXPECTED RESULT<br />
higher qualified throughput with unchanged organization semantics and unchanged API/SSE contract</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Fleet** | **Scale signal** | **Scale action** | **Application code change expected?** |
|----|----|----|----|
| Gateway | RPS/replica, CPU, event-loop lag, active streams, p99 overhead | Add replicas/CPU; keep stateless image/config contract. | No |
| Local guard | Queue age, inference p99, windows/s, GPU utilization/VRAM | Add guard replicas/GPUs; tune deployment batch profile. | No |
| Shared state | Ops/s, pool wait, failover headroom | Scale/cluster state service; retain same lease/snapshot semantics. | No |
| Provider/inference | TTFT, tokens/s, waiting requests, provider quota | Increase dedicated/provider capacity. | No |
| Audit/analytics | Producer backlog, durable write lag | Add partitions/workers/storage; remain async. | No |

# 8. Source basis and precedence

| **Source** | **How this runbook uses it** |
|----|----|
| 2026-09-07-end-to-end-architecture-hld.md | Target topology and request/control separation; historical \$5k operating point. |
| 2026-09-02-hot-path-cost-matrix.md | Corrected historical all-in cost and 1,064-RPS planning baseline; prompt egress correction. |
| 2026-08-27-FINAL-evidence-based-hot-path-plan.md | Detection defects, local GPU plan, measurement gates and window-count concerns. |
| 2026-08-25-FINAL-verification-and-platform-agnostic-scale-plan.md | Ordering, tenant isolation, prefetch-not-fanout, frontend/analytics verification. |
| Repository review: ansh @ e95f974dc500414b8f4db28038977fa9bf7deb44 | Current reviewed code still had Gemini/Bedrock paths, mixed policy semantics, output/timing inconsistencies and scalable-runtime work remaining. T00 must re-pin current head before execution. |
| User-reported TensorRT L4 test | ~2.14 ms p50 W1 component evidence only; T09/T18 must reproduce in release container and measure sustained multi-window throughput/p99. |
| OpenAI Python/Node official repositories/docs (checked 2026-09-16) | SDK base URL and SSE streaming expectations used for T11/T12 live compatibility. |
| TrueFoundry 2026 architecture/benchmark material | Comparison reference: in-memory enforcement/asynchronous logging and vendor-reported ~350 RPS/vCPU on a fake OpenAI upstream; not equivalent to AI Mesh full security. |
| Envoy Gateway / Envoy AI Gateway current docs | Reference for production proxy/control-data separation, streaming timeouts/body processing and horizontally managed data-plane concepts. |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p><strong>Final interpretation</strong></p>
<p>AI Mesh is not being built as a 1,064-RPS product. It is being built as a horizontally scalable gateway whose first cost-constrained deployment happens to be ≤$5,000/month. The release claim is the maximum qualified RPS actually measured inside that envelope with p99 &lt;20 ms and all selected controls operating. Future capacity growth must be a deployment/infrastructure exercise, not an application rewrite.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# **9. Frontend revamp: clean product experience, public landing page and operator console**

This track is part of the release program, not a later cosmetic project. It keeps the existing React/Vite application and backend APIs, but restructures the experience around clear information architecture, a restrained design system, truthful operational states and live end-to-end verification.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p><strong>Design intent</strong></p>
<p>Take inspiration from the clarity of modern AI infrastructure products such as TrueFoundry - strong product hierarchy, generous whitespace, code-first onboarding, clear proof points and progressive disclosure - while keeping an original ZeroShield visual identity. Do not copy TrueFoundry brand assets, wording or page composition. Product claims on the landing page must come from qualified AI Mesh evidence, never aspirational numbers.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## **9.1 Codebase findings that drive the redesign**

| **Observed current code** | **Why it matters** | **Revamp direction** |
|----|----|----|
| React 19 + Vite 7 + Tailwind 4, with Lucide, Motion, ECharts/uPlot and reusable UI primitives. | The stack is modern enough; a framework rewrite would add risk without fixing product structure. | Keep React/Vite. Build a stricter design system, route-level code splitting and reusable page patterns. |
| App.jsx protects / and switches most product views through ?tab=...; there is no public product landing route. | The app lacks a clean public product story and durable resource-oriented URLs. | Move console to /app/\*; make / public; preserve legacy ?tab links via deterministic redirects. |
| Sidebar exposes AI Mesh plus eight long nested module items, including technical 1.1-1.7 concepts. | Navigation reflects implementation modules rather than operator jobs. | Use task-oriented top-level navigation: Overview, Requests, Policies, Guardrails, Models, MCP/RAG, Simulator, Settings. |
| AIMeshFirewallOverview.jsx is ~55 KB; AttackSimulatorPanel.jsx ~61 KB; Header/Sidebar are ~20 KB each. | Large multi-purpose components increase visual inconsistency, regression risk and bundle coupling. | Split by domain panels and routes; lazy-load heavy charts/simulator; keep state/data hooks separate from visual components. |
| index.css imports three font families and uses radial gradients plus many module-specific color gradients. | The current theme can feel visually noisy and inconsistent with an enterprise security gateway. | One sans + one mono; neutral surfaces; one primary accent; semantic red/amber/green only for state. Remove rainbow module identity. |
| Existing visual harness already covers 12 surfaces x 2 themes x 4 widths, overflow, leaks and console errors. | There is valuable regression infrastructure to preserve. | Extend it with redesigned routes, landing page, accessibility checks, browser matrix and production-bundle performance budgets. |
| Firewall configuration already persists through /api/firewall/config/ and has per-section cards. | The control plane can be redesigned without changing policy semantics. | Recompose into a policy control center with progressive disclosure and explicit user-owned actions. |

## **9.2 Visual system and UX doctrine**

- Enterprise neutral first: white / near-white and deep-slate surfaces; one primary brand accent. No module-specific rainbow gradients or decorative radial backgrounds inside the authenticated console.

- Semantic color only: green = healthy/success, amber = warning/degraded, red = block/error/danger, blue/teal = selection or informational state. Color is never the only carrier of meaning.

- Typography: one primary sans family plus one monospace family for code/IDs. Prefer self-hosted or system-safe delivery to avoid layout shifts and three separate external font requests.

- Layout: 8 px spacing system, restrained 8-12 px radii, subtle borders, low-elevation shadows, generous whitespace and clear section hierarchy. Avoid oversized 24-28 px card radii as a default.

- Motion: 120-200 ms purposeful transitions only. No hover lift on every control. Respect prefers-reduced-motion and never animate high-density operational data unnecessarily.

- Progressive disclosure: show the decision and current state first; advanced tuning, raw rules and technical traces expand on demand.

- Truth before polish: every status card must be backed by real API state. Unknown/unavailable is rendered explicitly; never replace missing data with optimistic copy.

- Operator efficiency: common jobs should be reachable in \<=2 navigation actions from the app shell; destructive actions require context, confirmation and result evidence.

## **9.3 Target information architecture and route contract**

| **Route** | **Purpose** | **Primary content** |
|----|----|----|
| / | Public landing page | Product story, OpenAI-compatible integration, local guardrails, org control, architecture, verified proof points, deployment options, CTA. |
| /login | Authentication | Focused sign-in, SSO/OAuth, minimal distraction, clear tenant/product context. |
| /app/overview | Operator home | System posture, qualified traffic, blocks/redactions, latency, provider health, urgent actions. |
| /app/requests | Traffic / trace explorer | Search/filter requests, action/status, stage timing, provider, policy version, request detail drawer/page. |
| /app/policies | Policy control center | Organization policy sets, rule enablement, OFF/MONITOR/ENFORCE, versions, drafts/publish/rollback. |
| /app/guardrails/input | Input controls | Injection, PII/secrets, transformations, detector readiness, thresholds only where supported. |
| /app/guardrails/output | Output controls | Streaming vs strict mode, BLOCK/REDACT/FLAG/REWRITE where supported, detector/failure semantics. |
| /app/models | Models and routing | Connections, routing policy, failover, quotas, kill-switch / isolation, provider health. |
| /app/mcp-rag | MCP and RAG security | Registries, connectors, sandbox/policy status, retrieved-content controls, grounding capability state. |
| /app/simulator | Safe test workspace | Explicit scan-only vs generation modes, fixture selection, controlled load caps, result evidence. |
| /app/settings | Organization settings | Members/RBAC, API keys, audit/retention, organization metadata, appearance. |
| /app/profile | Personal settings | Profile, password/SSO status, sessions. |

Legacy compatibility: map existing /?tab=firewall-1-1 ... firewall-1-7, firewall-config, profile and settings URLs to the new routes with deterministic redirects. Preserve bookmarks during a defined deprecation window and instrument legacy-route usage before removal.

## **9.4 Public landing page specification**

| **Section** | **Required experience** | **Content rule** |
|----|----|----|
| Hero | Short value proposition, 1 primary CTA + 1 secondary CTA, restrained architecture visual. | Suggested direction: “One secure gateway for every AI request.” Mention OpenAI-compatible access, local guardrails and organization-owned policy control. No unqualified performance numbers. |
| Trust / proof bar | Verified facts only. | Use measured latency/RPS/cost after T23; before qualification show qualitative capabilities, not placeholder “99.99%” or invented customer logos. |
| Product pillars | 4-6 simple cards. | AI Gateway, Guardrails, Model Governance, MCP/RAG Security, Observability, Organization Policy Control. |
| How it works | Three-step diagram. | App/SDK -\> AI Mesh policy/security -\> customer model -\> output guard -\> app. Distinguish customer provider from platform security inference. |
| Code-first integration | Copyable Python and Node snippets. | Use real OpenAI SDK base_url patterns validated in T12. Code snippet must stay synchronized with supported API behavior. |
| Control & governance | Policy screenshots / interaction preview. | Show user-owned OFF/MONITOR/ENFORCE and supported actions. Do not imply unsupported categories exist. |
| Deployment / resilience | Simple deployment options and scale story. | Today’s \$5k envelope is not a code ceiling. Explain horizontal scaling without promising fleet numbers not yet qualified. |
| Final CTA + footer | Open Console / Sign In / Docs / Contact. | Clean, minimal footer with product, docs, security, privacy and status links. |

## **9.5 Frontend task index**

> **v2.1 CORRECTION (C17).** Dependencies re-mapped to the rebuild: UI04 → GW01/GW15 (not T12); UI05 → GW14, GW14b (not T03/T15); UI06 → GW09, GW14, GW14b (not T03/T07/T15); UI07 → GW05, GW07, GW13, GW14b (not T05/T06/T10); UI08 → GW06, GW11, GW12 (not T13/T14); UI12 → UI11, GW14b, GW20, T20, T21 (not T18/T19); UI13 → UI12, T22 **and GW23** (UI13 forbids changing security semantics, which GW23 does). The T22 ↔ UI12 cycle is resolved as T22 → UI12. Measured baseline for UI10: the production build is one JS chunk of 648,563 bytes gzip (1.85× the app-shell budget, 3.24× the landing budget; ECharts alone is 69% of the shell budget) — route-level code splitting is a prerequisite. The public /login page hard-codes "Uptime 99.99%", "SOC2 / ISO" and "10M+ calls"; §9.4's claim rule applies to it now. The :8180 console is a Vite dev server with a source bind mount.


| **Task** | **Work** | **Depends on** | **Exit proof** |
|----|----|----|----|
| UI00 | Freeze frontend baseline and user-flow inventory | T00 | Baseline screenshots, route map, bundle/API waterfall, current visual-test report |
| UI01 | Create the ZeroShield design system and remove theme noise | UI00 | Tokens, primitives, typography, semantic states, component examples |
| UI02 | Introduce public landing route and route-based console IA | UI00, UI01, T01 | / public, /app/\* protected, legacy redirect map |
| UI03 | Rebuild app shell, navigation, header and organization context | UI01, UI02 | Task-oriented nav, org switch/search/account, responsive shell |
| UI04 | Build the professional public landing page | UI01, UI02, T12 | Hero, product pillars, architecture, SDK snippets, verified claims |
| UI05 | Redesign overview dashboard around operator decisions | UI03, T03, T15 | Actionable KPIs, health, traffic, latency, urgent states |
| UI06 | Build Requests / Trace Explorer and truthful request detail | UI03, T03, T07, T15 | Search/filter, stages, provider bytes/action/policy correlation |
| UI07 | Rebuild Policies + Guardrails as the user-owned control center | UI03, T05, T06, T10 | Progressive disclosure, action semantics, draft/publish/rollback |
| UI08 | Redesign Models, routing, kill-switch, MCP/RAG and simulator | UI03, T13, T14 | Clear domain pages, safe destructive controls, explicit simulator modes |
| UI09 | Decompose monolithic frontend code and introduce route-level lazy loading | UI03-UI08 | Smaller domain components, isolated data hooks, code splitting |
| UI10 | Accessibility, responsive behavior, theme and performance hardening | UI01, UI09 | WCAG AA, browser matrix, bundle/CWV budgets, no overflow/leaks |
| UI11 | Extend visual + behavior regression harness for new routes | UI02-UI10, T02 | Pinned Playwright, real-backend visual matrix, a11y and error-state gates |
| UI12 | Run production-bundle Docker/staging E2E against real backend | UI11, T18-T21 | Live policy actions, cross-tenant tests, load coexistence, degraded states |
| UI13 | Canary the redesign and retire legacy UI/routes | UI12, T22 | Usage/error telemetry, staged rollout, rollback, legacy redirect retirement |

## **UI00 - Freeze the current frontend baseline and operator-flow inventory**

**Dependencies:** T00. Must finish before visual implementation begins.

### **Why this task exists**

The redesign must improve the actual product rather than replace working behavior with a prettier but incomplete shell. The current branch already has visual hardening and live-data fixes that must survive.

### **Implementation work**

139. Pin frontend source SHA, package-lock hash, served asset hash and production-bundle build command. Record which branch/image the staging console actually serves.

140. Capture current authenticated and unauthenticated routes, user roles, organization selection behavior, all major control forms, simulator paths and request-detail flows.

141. Generate baseline screenshots at 1440, 1024, 768 and 375 px in both supported themes using the existing visual harness; capture console errors, overflow and network request counts.

142. Record initial bundle/chunk sizes, first-load request waterfall, dashboard API request fan-out, hidden-tab polling behavior and current Core Web Vitals in staging.

143. Inventory all components \>15 KB and classify each by domain, data ownership and whether it should become a route, panel or primitive. Preserve existing live-data integrity fixes.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Production bundle identity | Serve the baked frontend through staging edge; match JS/CSS asset hashes to build manifest. | Browser, source and image match. | Stale/dev bundle or unknown asset provenance. |
| Surface matrix | Run current visual harness across all declared surfaces, widths and themes. | Every baseline result recorded, including known defects. | Missing surfaces or screenshots substituted for failed routes. |
| Operator walkthrough | Use a synthetic org to save policy, view request detail, run simulator, change model/routing and logout. | All current jobs mapped with request IDs/API calls. | A current capability has no mapped user flow. |
| Demand profile | Open overview, switch periods, hide tab, return, navigate quickly. | Network fan-out/polling/cancellation baseline captured. | Unable to explain duplicate or runaway requests. |

### **Failure scenarios that must be handled**

- Wrong environment or stale SPA served by nginx/CDN.

- Frontend only works with Vite dev server.

- Existing visual regression fixes disappear in redesign planning.

- Current user-control/API fields are not inventoried before UI changes.

### **Exit criteria**

- Baseline manifest complete.

- All existing routes/jobs have an owner and destination in the new IA.

- Current visual/network/performance evidence archived.

- No unowned critical flow remains.

### **Evidence package**

- frontend-baseline.json

- route-and-flow-map.md

- baseline screenshots/report.json

- bundle-report.json

- network-waterfall.har (sanitized)

- component-size inventory

### **Rollback / stop rule**

No functional changes. If provenance is unknown, stop and fix deployment identity first.

## **UI01 - Create the ZeroShield design system and remove theme noise**

**Dependencies:** UI00.

### **Why this task exists**

The present stylesheet mixes three font families, decorative gradients and many module-specific color identities. A professional gateway needs a coherent visual language that scales across dozens of states without becoming noisy.

### **Implementation work**

144. Create a single token source for color, spacing, type, radius, elevation, motion and chart palettes. Default proposal: neutral slate/white surfaces, one primary blue/cyan family, semantic green/amber/red only for state.

145. Reduce typography to one sans family and one monospace family. Prefer self-hosted or system-safe delivery; remove redundant Google Font imports and measure layout shift.

146. Unify Button, Card, Input, Select, Dialog, Badge, Tabs/SegmentedControl, Table, EmptyState, Skeleton, Toast, Tooltip, PageHeader and Status components with explicit variants.

147. Remove rainbow module gradients from authenticated surfaces. Module identity comes from icon + title + route, not a unique color family.

148. Define light and dark themes from the same semantic tokens; dark mode is not a separately designed neon theme. Ensure semantic contrast and focus states remain clear.

149. Create a small internal design-system showcase route available only in dev/staging for visual verification; exclude it from production navigation.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Token coverage | Render every primitive/state in the showcase through production CSS build. | No raw ad-hoc colors needed for standard states. | Components still require scattered hard-coded theme colors. |
| Contrast | Run automated contrast scan plus manual spot checks for text, controls, status badges and charts. | WCAG AA for normal text/controls; focus visible. | Any critical/serious contrast failure. |
| Theme parity | Toggle light/dark across showcase and key pages. | Meaning and hierarchy preserved; no unreadable state. | Status meaning changes or dark-only/light-only defects. |
| Motion | Enable reduced-motion OS setting and exercise interactions. | Nonessential motion removed; no usability loss. | Core navigation or state feedback depends on animation. |

### **Failure scenarios that must be handled**

- Brand drift into a TrueFoundry clone.

- Status color becomes decorative and ambiguous.

- Dark theme introduces gradients/glows absent from light theme.

- Changing primitive APIs breaks existing form behavior.

### **Exit criteria**

- Token file and primitive APIs versioned.

- No rainbow module styling in new routes.

- AA contrast/focus gates green.

- Design review approves professional/consistent appearance before page migration.

### **Evidence package**

- design-tokens.md

- component showcase screenshots

- contrast report

- theme matrix

- migration guide from old classes/primitives

### **Rollback / stop rule**

Feature-flag the new theme primitives. Old pages may keep old styles temporarily, but do not mix new tokens and legacy gradients within the same migrated page.

## **UI02 - Introduce a public landing route and resource-oriented console routing**

**Dependencies:** UI00, UI01, T01.

### **Why this task exists**

The current protected root plus query-tab navigation makes the product feel like one giant dashboard and leaves no real landing page. Resource routes improve clarity, deep links, testing and progressive loading.

### **Implementation work**

150. Make / the public landing page. Move authenticated product surfaces under /app/\* and keep /login and /oauth/callback public.

151. Implement explicit route objects for overview, requests, policies, guardrails/input, guardrails/output, models, mcp-rag, simulator, settings and profile.

152. Add legacy redirect mapping from every supported ?tab= value to the corresponding new route. Preserve existing query parameters that are meaningful to the destination.

153. Use route-level lazy imports and Suspense/error boundaries. Keep auth/org checks at the protected app boundary, not duplicated in every page.

154. Add not-found, forbidden, organization-required and service-unavailable pages that use real error state and recovery actions.

155. Ensure browser back/forward, refresh and copied deep links work directly through nginx/public edge.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Unauthenticated landing | Open / with no token through public staging hostname. | Landing renders; no protected APIs called. | Redirect loop/login-only root or data leak. |
| Protected deep link | Open /app/policies with no token, authenticate, then return. | Login flow preserves intended destination safely. | User lands on wrong org/page or loses state. |
| Legacy links | Open each old /?tab=... URL. | Deterministic redirect to matching new route. | 404, wrong module or silent fallback to overview. |
| Edge refresh | Hard refresh every new route through nginx/LB. | 200 app shell or expected auth flow; no server 404. | Client-only routing works only via in-app navigation. |

### **Failure scenarios that must be handled**

- Public page accidentally exposes tenant API calls.

- Legacy redirects allow arbitrary tab injection.

- Route migration breaks OAuth callback or stored redirect.

- Lazy chunk failure leaves blank screen without recovery.

### **Exit criteria**

- Public / works.

- All protected routes deep-link/refresh correctly.

- Legacy route map complete and instrumented.

- No protected data requested before auth/org resolution.

### **Evidence package**

- route manifest

- legacy redirect tests

- edge refresh matrix

- auth redirect traces

- chunk-load failure proof

### **Rollback / stop rule**

Keep legacy query routing behind a compatibility adapter until UI13 shows negligible usage. Revert route shell without changing backend APIs if necessary.

## **UI03 - Rebuild the authenticated app shell, navigation and organization context**

**Dependencies:** UI01, UI02.

### **Why this task exists**

Operators need a calm shell that makes the product hierarchy obvious. Current navigation exposes technical module numbering and long labels, while header/sidebar logic has grown large and stateful.

### **Implementation work**

156. Replace 1.1-1.7 navigation labels with task-oriented groups: Overview, Requests, Policies, Guardrails, Models & Routing, MCP & RAG, Simulator, Settings.

157. Keep organization selector, environment/context, global search, docs/help and user menu in a compact top bar. System health appears as a concise status indicator, not a decorative always-green card.

158. Use breadcrumbs and page headers for location; sidebar handles product areas only. Move secondary tabs into each domain page.

159. Collapse mobile navigation into an accessible drawer with focus trap, Escape close and scroll lock. Desktop collapsed mode uses tooltips and no hover-only flyout dependency.

160. Split Header and Sidebar into small presentational/navigation components plus hooks for auth/org/health; remove duplicated dark-mode class chains and conflicting style branches.

161. Preserve organization/user role visibility rules and make unauthorized navigation items absent or disabled with an explainable reason according to product policy.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Desktop navigation | Navigate all top-level routes at 1440/1024 widths with keyboard and pointer. | Active state/breadcrumb correct; \<=2 actions to common jobs. | Ambiguous active state, hidden route or hover-only access. |
| Mobile drawer | Run at 375/768, open/close drawer, tab through links and account menu. | No background focus/scroll, Escape works, focus returns. | Focus escapes or page becomes horizontally scrollable. |
| Org context | Switch synthetic org A/B with opposite policies. | Header context, routes and data refresh consistently. | Stale org data persists or cross-tenant state flashes. |
| Health state | Slow/fail backend health endpoint. | Neutral checking/degraded/offline state shown truthfully. | “Protected” stays green while backend is unreachable. |

### **Failure scenarios that must be handled**

- Organization switch during in-flight data fetch.

- Role change invalidates current page.

- Mobile resize during open drawer.

- Health endpoint slow or unavailable.

### **Exit criteria**

- Navigation IA approved by product/security.

- Responsive shell passes keyboard/mobile tests.

- No cross-org stale UI.

- Header/sidebar complexity decomposed with no feature loss.

### **Evidence package**

- navigation screenshots

- keyboard trace

- org-switch network trace

- role matrix

- header/sidebar component map

### **Rollback / stop rule**

Feature flag the new shell. If rollback occurs, keep new resource routes redirected to the legacy shell so bookmarks remain valid.

## **UI04 - Build the clean professional public landing page**

**Dependencies:** UI01, UI02, T12.

### **Why this task exists**

The product needs a credible public front door that explains what AI Mesh does before asking users to log in. The page should feel like a modern AI infrastructure product without copying another company’s identity.

### **Implementation work**

162. Implement the landing sections in 9.4 with a restrained white/neutral visual system and a single brand accent. Keep hero copy short and developer-focused.

163. Include a real OpenAI Python and Node integration snippet whose base_url/API path is validated by T12. Add copy buttons with accessible confirmation.

164. Use an original lightweight architecture illustration built from ZeroShield components/branding; avoid third-party screenshots or copied TrueFoundry graphics.

165. Only show quantitative proof points after they are sourced from release evidence. Until then, use capability claims such as OpenAI-compatible, local guard inference, organization-controlled policies and BYOK routing.

166. Lazy-load below-the-fold visuals. Keep charts and authenticated dashboard libraries out of the landing initial chunk.

167. Add appropriate metadata, social preview, favicon/branding, security/privacy links and clear Open Console / Sign In / Docs actions.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Cold landing | Load / in clean browser cache via staging CDN/LB. | No auth required; fast stable render; no console errors. | Dashboard bundle or protected API required for hero. |
| CTA paths | Exercise Open Console, Sign In, Docs and code copy. | Each leads to valid destination; auth users enter app cleanly. | Dead CTA or auth loop. |
| Claim audit | Compare visible performance/security claims to signed release evidence. | Every numeric claim has an evidence ID/date/profile. | Aspirational 100k, 99.99%, customer logos or unqualified \<20 ms shown as fact. |
| Responsive/SEO smoke | Render 375/768/1440 and inspect metadata/headings. | No overflow; one H1; sensible semantic structure. | Hero clipped, heading hierarchy broken or unreadable mobile code block. |

### **Failure scenarios that must be handled**

- Release evidence unavailable for metrics.

- Landing asset/CDN failure.

- Authenticated user returns to landing unexpectedly.

- Docs link/version diverges from SDK compatibility.

### **Exit criteria**

- Public landing route production-ready.

- No invented claims or copied competitor assets.

- SDK snippets verified.

- Landing performance budget passes UI10.

### **Evidence package**

- landing screenshots

- bundle waterfall

- claim-evidence ledger

- CTA trace

- metadata/SEO report

### **Rollback / stop rule**

Landing can roll back independently to a minimal static page. Never route public traffic directly to the authenticated overview as an emergency workaround.

## **UI05 - Redesign the overview dashboard around decisions, not decorative density**

**Dependencies:** UI03, T03, T15.

### **Why this task exists**

The current overview contains many colorful module cards and charts. A professional security gateway home should answer: Is traffic protected? What changed? What needs action? How is the gateway performing?

### **Implementation work**

168. Restructure overview into four zones: posture/health, traffic + actions, latency/provider reliability, and recent/high-priority findings.

169. Remove one-card-per-module rainbow treatment. Use a small number of neutral KPI cards and charts with consistent visual scales.

170. Make every metric link to a filtered destination (Requests, Policies, Models, Guardrails) so overview is a control surface, not a dead report.

171. Use data freshness, last-updated time and degraded/unavailable states explicitly. Never display zero as a substitute for failed analytics.

172. Keep chart count intentionally low. Lazy-load heavy analytics below the fold and cancel stale requests on period/filter changes.

173. Add a “Needs attention” panel for policy not ready, local guard unavailable, provider degraded, kill-switch active, audit backlog and configuration draft.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Normal org | Seed representative synthetic traffic and open overview. | KPIs reconcile to backend oracle and link to matching filters. | Dashboard values disagree with raw source or link nowhere. |
| Empty org | Open org with no traffic. | Useful empty state/onboarding; no fake trend lines. | Zero-data shown as operational success. |
| Backend analytics failure | Force one analytics endpoint 500/timeout. | Only affected panel shows error/degraded with retry. | Whole page fails or displays zero. |
| Rapid period changes | Switch 1h/24h/7d/30d rapidly and hide tab. | Superseded requests cancelled/deduped; no stale overwrite. | Old response wins or request storm occurs. |

### **Failure scenarios that must be handled**

- Very large datasets.

- Partial analytics outage.

- Policy/version changes while page visible.

- Hidden tab or slow client.

### **Exit criteria**

- Dashboard answers health/action questions without module-card clutter.

- Metrics reconcile to source data.

- Demand-side request amplification bounded.

- Responsive/visual/a11y gates green.

### **Evidence package**

- dashboard oracle comparison

- network concurrency trace

- empty/error screenshots

- link/filter reconciliation

### **Rollback / stop rule**

Keep API contracts unchanged. A visual rollback must not restore unbounded polling or known data-integrity defects.

## **UI06 - Build Requests / Trace Explorer and truthful request detail**

**Dependencies:** UI03, T03, T07, T15.

### **Why this task exists**

An industry gateway is debugged request-by-request. Operators need a fast searchable trace explorer that makes final action, detector execution, policy version, provider timing and stream outcome obvious.

### **Implementation work**

174. Create paginated/virtualized Requests table with time, status/action, model/provider, latency, org-scoped tags, stream/non-stream and risk/finding summary.

175. Implement server-backed filters and URL-addressable query state: time range, action, detector, model, provider, error, policy version and request ID.

176. Redesign request detail into sections: summary, action/findings, stage timing, policy snapshot, provider/stream metadata and authorized sanitized content references.

177. Distinguish executed, skipped, unavailable and degraded detectors. Do not render skipped as “passed”.

178. Show T_fw_addon, provider time and release lag separately using T03 definitions. Never recompute a different frontend latency formula.

179. Use bounded pagination and lazy detail fetch. Do not fetch full raw metadata for every row.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Trace reconciliation | Send allow/redact/block/flag fixtures through staging and open each request ID. | UI matches provider recorder, audit and backend trace. | Badge/timing differs from backend evidence. |
| Large dataset | Seed \>=100k synthetic request records and browse/filter pages. | Stable memory/interaction; no full-table browser materialization. | Browser freezes or downloads unbounded dataset. |
| Authorization | Attempt cross-org request ID and raw-content reference. | 404/403 according to contract; no metadata leak. | Foreign record or timing/details visible. |
| Partial stream | Abort SSE after content release and inspect record. | Partial/aborted state explicit; not shown as success/block-withheld. | Wire outcome misrepresented. |

### **Failure scenarios that must be handled**

- Trace missing stage timestamps.

- Content reference expired/unavailable.

- Audit record lags live trace.

- Request retried with multiple attempt IDs.

### **Exit criteria**

- Request explorer is source-of-truth friendly, fast and tenant-scoped.

- No unbounded client query.

- Timing/action semantics match T03/T06/T11.

- Cross-org canary test passes.

### **Evidence package**

- request explorer screenshots

- 100k-row browser profile

- request-ID reconciliation matrix

- authorization captures

### **Rollback / stop rule**

If detail schema changes, keep a versioned adapter. Never fall back to rendering raw backend payloads directly.

## **UI07 - Rebuild Policies and Guardrails as the organization-owned control center**

**Dependencies:** UI03, T05, T06, T10.

### **Why this task exists**

The user explicitly wants full control over content-security behavior. The frontend must make that authority understandable and exact, not bury it across many cards with ambiguous global modes.

### **Implementation work**

180. Create a Policies page with versioned drafts, published version, last editor/time, diff/preview and rollback where backend supports it.

181. For each supported rule, show OFF / MONITOR / ENFORCE and the supported transport action(s). Disable unsupported combinations with an explanation rather than hiding them.

182. Separate Input Guardrails and Output Guardrails. Output page must explicitly show streaming/pre-release versus strict withholding modes and their latency/behavior trade-offs.

183. Use progressive disclosure: common safe settings first; advanced thresholds/model details only when the backend exposes and supports them.

184. Add an “Effective execution plan” preview showing what will actually run for this org, including detector readiness and failure behavior.

185. On save/publish, confirm persisted config and applied gateway policy version. Never show success solely because the control API returned 200 if runtime propagation failed.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| OFF→MONITOR→ENFORCE→OFF | Change one synthetic injection rule through each state, publish, send same fixture each time. | Observed runtime exactly matches selection and policy version. | Hidden detector/action or stale runtime behavior. |
| Mixed actions | Set PII=REDACT, credentials=BLOCK, injection=FLAG and send co-matching fixtures. | Final transport action follows central resolver; all findings visible. | One card silently overrides unrelated rules. |
| Output mode | Switch streaming vs strict mode and run output canary. | UI describes and wire behavior matches selected mode. | Strict mode leaks bytes or streaming is mislabeled whole-response block. |
| Propagation failure | Interrupt policy sync after publish. | UI reports pending/degraded/not-applied; no false “Protected”. | Save toast claims active state while gateway runs old version. |

### **Failure scenarios that must be handled**

- Unsupported action combination.

- Detector unavailable.

- Policy conflict/version race.

- User navigates away with unsaved changes.

- Two admins edit same policy version concurrently.

### **Exit criteria**

- Every content-control choice is understandable and backend-verifiable.

- No hidden content-security floor is presented as user-controllable.

- Runtime version visible after publish.

- Mixed-action and failure tests pass.

### **Evidence package**

- policy UI screenshots

- action capability matrix

- version propagation trace

- wire/provider/audit joins

- conflict handling evidence

### **Rollback / stop rule**

Keep server-side enforcement authoritative. If frontend rolls back, existing published policy remains unchanged and safe.

## **UI08 - Redesign Models, routing, kill-switch, MCP/RAG and simulator workflows**

**Dependencies:** UI03, T13, T14.

### **Why this task exists**

These are operational workflows with very different risk profiles. They should not be squeezed into generic cards or technical module numbering.

### **Implementation work**

186. Create Models & Routing page with provider/model connections, health, routing strategy, fallback, quota and per-model isolation/kill-switch controls.

187. Put destructive kill-switch/isolation actions in a clear emergency-control area with confirmation, target scope and resulting effective state.

188. Combine MCP and RAG into one security area with separate sub-tabs for MCP registry/sandboxes and RAG/vector connections, each showing policy/readiness scope.

189. Rebuild Simulator as an explicit test workspace: scan-only and generation are separate modes, with clear cost/side-effect labels and authorized maximum concurrency.

190. Reuse the Requests explorer for simulator results instead of maintaining a separate visual language for findings.

191. Replace vendor-specific leftovers such as “Bedrock Test” naming with capability-based local/runtime terminology after backend migration.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Kill-switch | Activate one synthetic model kill-switch during live allowed traffic. | New matching traffic denied/rerouted exactly per contract; UI state updates within freshness bound. | Wrong model/org affected or stale green state. |
| Routing/fallback | Degrade primary synthetic provider and observe controlled fallback. | UI shows selected route/fallback reason/attempts accurately. | Frontend shows requested model when another served without indication. |
| MCP/RAG | Run synthetic tool call and retrieved malicious/sensitive content. | Correct domain findings/actions and tenant scope visible. | Tool/RAG path bypass or cross-org data. |
| Simulator modes | Run scan-only then generation with same fixture. | Scan-only causes zero provider call; generation records provider usage. | UI claims scan-only while provider is billed/called. |

### **Failure scenarios that must be handled**

- Provider connection missing secret.

- MCP server unreachable.

- Vector store slow.

- Emergency action confirmation dismissed/retried.

- Simulator burst exceeds approved cap.

### **Exit criteria**

- Operational domains are clear and separate.

- Dangerous actions have scope/evidence.

- Simulator modes are truthful.

- No old provider/vendor naming misrepresents local-only architecture.

### **Evidence package**

- routing/fallback traces

- kill-switch timeline

- MCP/RAG request joins

- simulator provider-call proof

- destructive-action screenshots

### **Rollback / stop rule**

Roll back page composition without changing live routing/policy state. Destructive-action backend APIs remain protected independently of UI.

## **UI09 - Decompose monolithic frontend code and add route-level lazy loading**

**Dependencies:** UI03-UI08.

### **Why this task exists**

The current app imports many large pages/components directly. The revamp should improve maintainability and startup cost without a framework migration.

### **Implementation work**

192. Split each domain into route component, data/query hooks, pure presentation components and shared primitives. Avoid \>15-20 KB multi-purpose files unless justified by generated/static data.

193. Use React.lazy/dynamic imports for public landing, authenticated domains, heavy charts and simulator. Keep app-shell/auth bundle small.

194. Move API lifecycle logic out of visual components into named hooks/services with cancellation and dedupe semantics. Keep tenant/org context explicit in every query key.

195. Standardize page shells, section headers, filter bars, data tables, drawers/detail panels, status banners and empty/error states.

196. Remove dead/legacy frontend components only after route/use graph and visual/E2E coverage prove they are unused.

197. Create bundle analyzer output in CI and fail on accidental imports that pull authenticated analytics/chart libraries into the landing chunk.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Cold public bundle | Load / and inspect chunks. | No dashboard chart/simulator bundles downloaded before interaction. | Large authenticated bundle eagerly loaded. |
| Cold app route | Load /app/policies directly after auth. | Only shell + policy-route dependencies load initially. | Overview/simulator bundles load unnecessarily. |
| Rapid navigation | Navigate across routes during slow chunk/network delivery. | Loading/error boundaries stable; no stale data flash. | Blank screen or mixed-org old route content. |
| Dead-code removal | Run route/visual/E2E suite after deleting legacy module component. | No import/runtime regression. | Removal breaks hidden workflow or legacy redirect. |

### **Failure scenarios that must be handled**

- Lazy chunk 404 after deploy.

- Old client references removed hashed asset.

- Query cancellation on unmount.

- Shared component API drift.

### **Exit criteria**

- Route code splitting measured.

- No public-to-private bundle bleed.

- Large components decomposed where useful.

- Deployment handles hashed chunk cache safely.

### **Evidence package**

- bundle analyzer report

- chunk waterfall

- component map

- dead-code report

- navigation stress trace

### **Rollback / stop rule**

Keep one previous frontend asset set available during rollout or configure cache strategy so old HTML never references deleted chunks.

## **UI10 - Accessibility, responsive behavior, theme and frontend performance hardening**

**Dependencies:** UI01, UI09.

### **Why this task exists**

A clean design is not complete if keyboard users, narrow screens or slower machines cannot operate it. This task sets measurable product-quality gates.

### **Implementation work**

198. Adopt WCAG 2.2 AA as the default frontend accessibility target for the redesigned routes. Add semantic landmarks, label associations, focus management, table headers and live-region announcements where needed.

199. Extend responsive matrix to 375, 768, 1024, 1440 and 1920 px. Test browser zoom 200% on core control flows and long labels/data.

200. Pin a Playwright version in dev dependencies for release acceptance instead of relying on an arbitrary npx cache. Add axe-core or equivalent automated a11y checks while retaining manual keyboard verification.

201. Set initial performance budgets after UI00 baseline. Default target direction: landing initial JS \<=200 KB gzip, app shell \<=350 KB gzip, route lazy chunks bounded; any exception documented with reason. Heavy chart code must be lazy.

202. Run Lighthouse/Web Vitals in a controlled production build: target LCP \<=2.5 s, CLS \<=0.1, INP \<=200 ms on the agreed test profile; record rather than silently waive if staging topology dominates.

203. Keep no-horizontal-overflow, no-secret-in-DOM, no uncaught errors and both-theme gates from the current visual harness.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Keyboard only | Operate login, nav, policy edit/publish, request detail, simulator and kill-switch without mouse. | Logical focus, visible focus, no trap except intended modal. | Unreachable control or lost focus. |
| Screen/a11y scan | Run automated a11y on every route/state plus manual landmarks/names review. | Zero serious/critical automated issues; manual critical flows pass. | Serious/critical issue or unlabeled destructive control. |
| Responsive/zoom | Run width matrix + 200% zoom. | No content loss/overlap/horizontal page overflow. | Hidden action, clipped dialog/table, inaccessible drawer. |
| Performance | Cold-load landing and selected app routes with production assets under agreed network/CPU profile. | Budgets and CWV target met or explicitly blocked before release. | Performance regression ignored because backend is fast. |

### **Failure scenarios that must be handled**

- Very long organization/model/policy names.

- Large numbers/time zones/locales.

- Reduced motion/high contrast.

- Slow CPU/network.

- Missing icon/font asset.

### **Exit criteria**

- Accessibility and responsive gates green.

- Performance budgets signed and passing.

- Theme parity stable.

- No leak/console/overflow regressions.

### **Evidence package**

- axe report

- keyboard checklist/video

- visual matrix

- Lighthouse/Web Vitals report

- bundle budget report

### **Rollback / stop rule**

If a visual feature breaks a11y/performance gates, remove or simplify that feature; do not weaken the gate to preserve decoration.

## **UI11 - Extend the visual and behavior regression harness for the redesigned product**

**Dependencies:** UI02-UI10, T02.

### **Why this task exists**

The repository already has a valuable real-browser visual harness. The redesign should evolve it into a deterministic release gate rather than replace it with brittle pixel snapshots or mocked stories.

### **Implementation work**

204. Update surfaces.json for every public/protected redesigned route and required states. Keep both themes and the existing width matrix; add 1920 if practical.

205. Pin Playwright/Chromium versions in the release test image. Keep screenshot artifacts for human review but gate on semantic/behavior constraints rather than pixel-perfect equality.

206. Add axe checks, keyboard smoke, route/deep-link validation, organization context, no-leak DOM scan, console/page errors, main overflow and chart/render presence.

207. Add state fixtures through real APIs: normal, empty, loading, 400/401/403/429/503, policy pending, guard unavailable, provider degraded and partial stream.

208. Run against the baked production frontend and real Docker control/gateway services; do not mock route responses for acceptance.

209. Publish a single report that links screenshot, route, theme, width, API state and failure reason.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Full matrix | Run all routes x themes x widths against stable staging dataset. | Zero gated regressions; screenshots produced. | Any missing main, overflow, leak, console/page error or a11y critical. |
| Error-state matrix | Inject supported API failures via staging fault controls. | Each page shows correct scoped recovery/error state. | Generic green/zero fallback or whole-app crash. |
| Auth expiry | Expire token during app use. | Safe re-auth flow; intended route preserved; no infinite request storm. | Silent failures or protected content remains accessible. |
| Chunk deploy | Deploy new frontend while old tab remains open, then navigate to lazy route. | Graceful reload/recovery on missing old chunk. | White screen / unhandled chunk-load error. |

### **Failure scenarios that must be handled**

- Staging DB pressure causes auth flake.

- Network resource failure.

- Theme persistence corruption.

- Old localStorage keys conflict with redesign.

### **Exit criteria**

- Regression harness covers all release routes/states.

- Pinned reproducible browser environment.

- No mocks in acceptance path.

- Reports actionable and linked to route/state evidence.

### **Evidence package**

- updated surfaces.json

- Playwright image manifest

- regression report

- screenshots

- fault-state matrix

### **Rollback / stop rule**

Keep the previous harness until the new one passes the same old surfaces. Never delete regression coverage before equivalent new coverage exists.

## **UI12 - Run production-bundle Docker/staging E2E against the real backend under load**

**Dependencies:** UI11, T18-T21.

### **Why this task exists**

This is the frontend release gate. The redesigned console must prove real user control, correctness and stability while the gateway is doing meaningful work.

### **Implementation work**

210. Build the exact production frontend image from the candidate commit and serve it through the staging nginx/LB chain against the candidate control/gateway/local-guard stack.

211. Create two synthetic organizations with opposite policy/routing states and representative seeded traffic. Use real APIs and controlled provider recorder - no response mocks.

212. Run browser flows for login, org switch, overview, request detail, policy OFF/MONITOR/ENFORCE, mixed actions, output mode, model/routing, kill-switch, MCP/RAG and simulator.

213. Run backend controlled load at the current qualified serving-unit/fleet plateau while Playwright executes a small but realistic operator flow. Measure whether dashboard/API demand moves gateway p99 or control memory materially.

214. Inject guard unavailable, policy propagation delay, analytics failure, provider failure, 429/503 overload and partial SSE while operators are using the UI.

215. Reconcile every security-affecting UI action to persisted API config, applied policy version, provider recorder, request trace/audit and visible result.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| User authority | For each supported content control run OFF→MONITOR→ENFORCE→OFF from UI, then same fixture. | Actual detector/action/provider behavior follows UI selection exactly. | Saved UI state differs from runtime. |
| Cross-tenant | Operate org A/B in separate browser contexts under concurrent traffic. | No policy/data/request-ID bleed. | Any foreign state/content appears. |
| Load coexistence | Run dashboard/request explorer/policy publish while backend load is active. | Frontend/control activity stays within signed impact bound; gateway p99 remains qualified. | UI polling/queries materially destabilize gateway. |
| Degraded truth | Inject guard/policy/provider/control failures. | UI shows exact unavailable/degraded/pending state and recovery. | Green/protected/allow displayed for unknown execution. |
| Long session | Operate for \>=60 min with route/filter changes and hidden-tab periods. | No monotonic memory/request growth; session/auth behavior correct. | Leak, runaway polling or repeated stale data. |

### **Failure scenarios that must be handled**

- Browser refresh during policy publish.

- Two admins edit concurrently.

- Gateway replica restart.

- Control worker recycle.

- Network drop during save.

- Stale service worker/browser cache if introduced.

### **Exit criteria**

- All critical operator journeys pass on production bundle/live backend.

- Frontend user-control evidence matches backend/provider/audit.

- No tenant leak or misleading degraded state.

- UI activity does not invalidate gateway performance envelope.

### **Evidence package**

- Playwright traces/screenshots

- sanitized HAR/network records

- policy-version timeline

- provider/audit joins

- load coexistence metrics

- browser memory/network profile

### **Rollback / stop rule**

Frontend may roll back independently only when backend schema remains compatible. Never disable enforcement or revert tenant-isolation behavior because the new UI fails.

## **UI13 - Canary the redesign and retire legacy UI/routes safely**

**Dependencies:** UI12, T22.

### **Why this task exists**

A full visual rewrite should not be a one-shot cutover. Canary data is needed for hidden workflows, browser differences and operator adoption before removing compatibility paths.

### **Implementation work**

216. Release the redesigned frontend behind a server-side or deployment-level cohort flag to internal/staging users first, then approved production cohorts.

217. Track route errors, chunk-load failures, API error distribution, navigation dead ends, policy-save failures and old-route usage. Do not collect sensitive content for UX telemetry.

218. Keep backend APIs and security semantics unchanged during the visual canary. Functional changes require their own task/gate.

219. Advance 5%→25%→50%→100% only when live errors/critical workflows and support feedback meet the signed thresholds.

220. Keep old frontend asset tuple and route compatibility available for immediate rollback during canary.

221. After sustained 100% success, retire legacy ?tab renderer and dead components only after usage proves redirect paths are sufficient.

### **Live Docker / staging acceptance tests - required to exit**

| **Scenario** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| Canary rollback | Force a frontend-only defect in isolated canary and roll back asset/image. | Backend state preserved; old UI remains compatible. | Rollback requires policy/backend downgrade. |
| Legacy bookmark | Use stored old ?tab URL after 100% new UI rollout. | Redirects correctly with telemetry. | Broken bookmark/404. |
| Browser mix | Smoke supported Chrome/Firefox/WebKit or declared browser matrix. | Critical flows pass in supported set. | Unsupported behavior not documented or critical browser failure. |
| 100% observation | Observe full rollout for signed period with support/error metrics. | No unresolved critical UX/security truth defect. | High save/route/chunk error or operator cannot complete critical workflow. |

### **Failure scenarios that must be handled**

- Old/new asset cache mismatch.

- Frontend schema drift during backend deploy.

- Feature flag cohort changes mid-session.

- Legacy route still externally documented.

### **Exit criteria**

- 100% redesigned UI stable for signed observation window.

- Legacy route usage low/understood.

- Rollback rehearsed.

- Old components removed only after evidence.

### **Evidence package**

- canary cohort report

- error/route telemetry

- rollback proof

- browser matrix

- legacy usage report

- final removal diff

### **Rollback / stop rule**

Rollback the frontend image/cohort only. Keep backend policy/security state intact. Compatibility redirect stays until separately approved for removal.

## **9.6 Cross-browser / responsive / live-state qualification matrix**

| **Dimension** | **Required matrix** |
|----|----|
| Browsers | Pinned Chromium for release gate; smoke current Firefox + WebKit/Safari-equivalent where supported by CI. Declare supported browser versions. |
| Viewport | 375, 768, 1024, 1440, 1920 px; 200% zoom on critical forms and tables. |
| Themes | Light + dark if both remain supported. Theme switch must preserve semantic state and not reload/reset policy forms. |
| Auth | Logged out, normal org user, org admin, platform admin/superuser where product supports; expired token; revoked session. |
| Org state | Empty org, populated org, two opposite-policy orgs, policy-not-ready, pending publish, stale version. |
| Gateway state | Healthy, overloaded 429/503, local guard unavailable, provider degraded, kill-switch active, partial SSE, audit lag. |
| Data size | 0 rows, normal, \>=100k synthetic request records for explorer, long names/labels, many models/connectors. |
| Network | Normal, slow/high-latency, interrupted save, client offline/reconnect, missing lazy chunk after deploy. |

## **9.7 Frontend release exit gate**

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p><strong>Frontend release PASS only when all conditions are true</strong></p>
<p>Public landing is clean and factual; authenticated IA is task-oriented; organization controls are byte/runtime-verifiable; no cross-tenant state appears; visual/a11y/performance gates pass; production-bundle Playwright flows pass against live staging; degraded states remain truthful; frontend analytics/polling do not materially disturb the qualified gateway; legacy route rollback is proven.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

- Do not declare the redesign done from screenshots or a dev-server demo.

- Do not ship invented performance/customer metrics on the landing page before T23 evidence exists.

- Do not change security semantics to make controls visually simpler; UI must represent the real resolver/action contract.

- Do not let marketing/landing assets pull charting, auth or tenant data into the public initial bundle.

- Do not use frontend-only state as the source of truth for policy, kill-switch, routing or guard readiness.

- T22 and T23 cannot pass until UI12 passes on the same candidate frontend/backend contract.

## **9.8 Frontend source/reference basis**

- Repository reviewed: AnshSinghal-CyberUltron/AI_Mesh_Firewall, branch ansh, commit e95f974dc500414b8f4db28038977fa9bf7deb44 (re-pin at T00 before implementation).

- Current frontend stack observed: React 19, Vite 7, Tailwind CSS 4, Motion, Lucide, ECharts/uPlot, existing UI primitives and visual regression harness.

- Current route behavior observed in App.jsx: protected / dashboard with ?tab= switching; public /login and /oauth/callback; no dedicated public landing route.

- Current visual regression harness already audits 12 surfaces across light/dark and 1440/1024/768/375 widths for overflow, leaks, JS errors and chart regressions. Extend rather than discard it.

- External design inspiration reviewed 16 September 2026: TrueFoundry public homepage, AI Gateway page and Product Tour. Use only high-level patterns such as module clarity, code-first onboarding, whitespace and progressive disclosure; retain original ZeroShield branding/copy.

# 10. Gateway backend replacement: clean-room rebuild and single-cutover migration

> **v2.1 CORRECTION (C10, C19).** The audited baseline `ansh @ 2a657fad` cannot serve chat: every chat request with a routing catalogue returns HTTP 500 because main.py:4745 imports `catalog_row_is_display_alias`, which llm_router.py only gains in commit 2ed687a6. All end-to-end evidence in v2.1 was therefore collected at `revamp @ 52a584e9`, where every file on the tested paths is byte-identical except llm_router.py and pipeline_trace.py. There is one baseline pin for the rebuild: `revamp` HEAD at the moment GW04 starts, correlated to the deployed image digest (GW00).


| This section supersedes the repair-in-place assumption. Sections 1–8 defined \*what\* the gateway must do; §4's T00–T26 assumed the current gateway/ai_mesh_gateway package would be hardened into compliance. That assumption is withdrawn. The backend is rebuilt clean as gateway-v2, developed in parallel against a frozen wire contract, proven by shadow replay, and cut over in one event. §10.13 maps every T-task onto the new track so nothing in §4 is lost. |
|----|

Baseline re-pinned for this section: ansh @ 2a657fad ("fix(t01): honor PII-off as a true bypass and classify AKIA as secret"), re-fetched and re-audited on 17 September 2026. Note that revamp @ 877c27a8 is a \*later\* snapshot by commit time and is the branch carrying this runbook, the V3 runbook and staging/t02/. GW00 resolves which of the two is the true baseline before any code is written.

## 10.1 Why the backend is replaced rather than repaired

The decision does not rest on code aesthetics. It rests on five defects reproduced by executing the real production functions in this repository, plus a structural measurement that explains why fixing them individually does not converge.

### 10.1.1 Defects reproduced by execution

> **v2.1 CORRECTION (C14).** Re-executed at 2a657fad and HEAD (unit and end-to-end, real FastAPI app): P1, P2, P3 (unit), P6 matrix (unit, lines 388–402/404 exact) and P7 are **CONFIRMED**, and P7 holds end-to-end on both output paths. Corrections: P8's printed probe `[^]+` is not a valid regex (the intended pattern is a backtick pair, which does match 5/5 benign prompts), and P8 is a scanner.py probe, not enforcement.py. P6's consequence ("every response gets the same treatment … located precisely") holds only at unit level: with the compose-default Bedrock provider the degraded branch is never reached; with Gemini the outcome varies by path and content. The real flip-flop mechanisms are Gemini output degradation and the input breaker returning 503 from the 6th request. "Block/True releases content" is confirmed under Gemini, non-stream, with a byte-changing co-finding — a raw SSN is delivered while audit records "redact pii".


Each row below was produced by importing gateway/ai_mesh_gateway/enforcement.py at ansh @ 2a657fad and calling the shipped function directly. These are not readings of the code; they are its output.

| **ID** | **Probe executed** | **Result** | **What it means** |
|----|----|----|----|
| P1 | resolve_enforcement("block", org_policy_action="allow") | "block" | An organization's explicit ALLOW cannot suppress a scanner recommendation. Same result for "flag" and "monitor". |
| P2 | max_action("allow","block","allow") | "block" | The mechanism is a severity maximum across \[policy, recommendation, default\], not the precedence the module docstring promises at enforcement.py:7-11 ("Precedence (highest wins): 1. Org policy"). |
| P3 | enforce_output(verdict_action="block", scan_degraded=True) | "redact" | A terminal output BLOCK is silently downgraded whenever the output scan is degraded. Control with scan_degraded=False returns "block". |
| P6 | Full enforce_output matrix across verdict_action × scan_degraded | see below | When the output scan degrades, the emitted action becomes **independent of the verdict**. |
| P7 | enforce_output(verdict_action="block", enforcement_mode="monitor") | "block" | MONITOR does not neutralize enforcement on the output path. A tenant who selected observation-only still blocks. |
| P8 | re.search(r"\[^\]+", …)\` against five benign inline-code prompts | 5 / 5 match | ATTACK_PATTERNS\["command_injection"\] contains the literal pattern \` \[^\]+ \`\` — any backtick pair. |

The P6 matrix is the single most damaging result, because it converts a security control into a coin flip:

| **verdict_action** | **scan_degraded** | **resolved action** | **assessment** |
|----|----|----|----|
| block | False | block | correct |
| block | True | **redact** | security downgrade — content is released that policy said to withhold |
| flag | False | flag | correct |
| flag | True | **redact** | false redaction — content is mutated that policy said to pass |
| allow | False | allow | correct |
| allow | True | **redact** | false redaction on clean traffic |

enforcement.py:388-402 returns the degraded decision **before** verdict_action is read at :404. So during any Bedrock/Gemini flap, every response gets the same treatment regardless of what was actually found. The same prompt, sent twice, receives two different dispositions depending on the health of a third-party API. **This is the reported symptom "sometimes the prompt passes, sometimes not", located precisely.**

### 10.1.2 The label mismatch that disarms the fail-closed contract

> **v2.1 CORRECTION (C14).** Confirmed by driving the real scanner with a failing client: all degraded verdicts carry threat_type "scanner_degraded" and _is_tier2_degraded_verdict returns False. The emitted reason codes are client_error and parse_failure_conservative (parse_failed and empty_response are accepted but never emitted). The consequence is overstated: the degraded pass is visible (client metadata flag/scanner_degraded and request telemetry) and the default tier2_strict=True turns a sustained outage into HTTP 503 from the 6th request. There is no "blanket redact" on the output path under Bedrock.


main.py:5447-5454 defines \_is_tier2_degraded_verdict to return true when threat_type == "bedrock_degraded" or reason_code starts with "degraded". The scanner emits its degraded verdict at scanner.py:2464-2477 with threat_type = "scanner_degraded" and a reason_code drawn from meta\["decision_reason"\] — typically client_error, parse_failed or empty_response (scanner.py:2119-2126).

Neither condition is satisfied. tier2_degraded=False is therefore passed to the resolver at main.py:9450, the PIPELINE-0006 fail-closed contract at enforcement.py:309-314 never arms, and the verdict is then discarded entirely by the adapter described next. A semantic-scanner outage produces a **silent full pass** on the input path and a **blanket redact** on the output path, simultaneously.

### 10.1.3 The adapter that discards findings before they reach the resolver

> **v2.1 CORRECTION (C14).** Confirmed for flag-level Tier-2 verdicts (obfuscation, risk_score 0.40–0.69, scanner_degraded, other non-threat categories): they resolve to allow; the finding stays visible in client metadata and telemetry, the enforcement decision is lost. The scanner pre-escalates threat categories to block before the adapter runs. The `injection` vs `prompt_injection` divergence is confirmed as a mechanism (400 vs 200 at confidence 0.30), but the guard's own schema never emits bare `injection`, and the effect is over-blocking, not bypass.


\_scanner_kwargs_for_enforcement (main.py:895-948) returns an all-None dictionary unless one of three predicates holds. A Tier-2 verdict whose threat type falls outside \_PLATFORM_FLOOR_SCANNER_THREATS or \_TIER2_INJECTION_THREATS, and whose action is flag or monitor, is dropped before the resolver ever sees it — and resolve_and_enforce then resolves allow on an empty input.

The two threat-type sets are also divergent, which we confirmed by comparing them directly:

| **Set** | **Members** |
|----|----|
| main.\_TIER2_INJECTION_THREATS | goal_hijacking, injection, jailbreak, prompt_injection |
| enforcement.\_INJECTION_THREAT_TYPES | goal_hijacking, jailbreak, prompt_injection |
| Present in main only | **\`injection\`** |

A Tier-2 verdict labelled injection is admitted by the adapter and then bypasses the confidence/threshold gate at enforcement.py:272-279 that the other three labels are subject to. Two spellings of the same concept, two different security outcomes.

### 10.1.4 Structural measurement: why individual fixes do not converge

> **v2.1 CORRECTION (C14).** AST re-measurement at 2a657fad: main.py 17,452 lines (confirmed) but 869,078 bytes (808 KB is an older commit); proxy_chat 4,990 lines def-to-end (5,027 included 37 module-level lines), 95 returns (87 its own), 319 if/elif statements (339 counted continuation lines), 49 exception handlers of which 20 broad, maximum block nesting 9, 33 in-place mutations of `body`, 20 stage_metrics writes; test corpus 496 files / 105,248 lines (confirmed). Not reproducible under any definition at any audited commit: "283 decision sites / 25 modules" (narrowest principled definition: 264 sites / 21 modules), "72 in mcp_proxy", "144 env vars / 232 sites" (207 names / 241 sites with config.py wrappers). The CI workflow runs 4 test files (709 items), path-filtered, and failed on all 40 recorded runs. The structural conclusion stands.


Each defect above is individually a small patch. The reason they keep recurring is structural, and it is measurable.

| **Measurement** | **Value at ansh @ 2a657fad** |
|----|----|
| main.py | 17,452 lines / 808 KB, 213 top-level symbols, 35 routes |
| proxy_chat — one function | **5,027 lines** |
| — exit points | 95 return statements |
| — branch points | 339 if/elif |
| — exception handlers | 50, of which 17 are broad except Exception |
| — maximum nesting depth | 12 |
| — in-place mutations of body | 28 |
| — writes to the shared stage_metrics dict | 20 |
| Block/terminal-decision sites | **283 across 25 modules** |
| Distinct Tier-2 enablement resolvers | **4 implementations, 6 call paths** |
| Distinct environment variables read | 144 across 232 call sites |
| Test corpus | 496 Python test files, 105,248 lines |
| Test files actually run by CI | **2** |

A 5,027-line function with 339 branches and 95 exits has no reviewable state space. There is no point at which a reader can say what is true about a request. The 283 decision sites mean a correctness fix applied at one of them is silently contradicted at another — which is exactly the history recorded in this repository's own plan documents, where the same classes of defect have been found, fixed and refound across five review cycles.

The 105,248 lines of tests are not a counter-argument; they are the proof. That corpus coexists with every defect in §10.1.1. Tests cannot rescue an architecture that has no invariants for them to assert.

### 10.1.5 Configuration and isolation defects the rebuild must not inherit

> **v2.1 CORRECTION (C14) and new defects.** Row 1: the real inheritance path is get_config's fallback (:295–301) for an org with no row of its own or an empty slug (it receives the entire platform dict, Tier-2 on); the :687 merge cannot supply enforcement_mode/tier2_enabled. Row 5: each posture is confirmed but "removes rate limiting and blocks all traffic" at once is not reproduced (auth fails closed first); the real defect is a 1–2 s Redis-latency band in which TPM/model-RPM limits vanish. Row 7: "29 passes" is the 64-byte row of the header table; the shipped 160-byte setting gives 12–38 by content. Row 9: non-stream only and only when an operator selects rewrite (it then sends RAW output PII to the model). **New defects found by execution:** PII in system messages or split across text parts reaches the provider raw while the trace shows it masked; for a zero-policy org with tier2 unset, streaming delivers raw PII that non-streaming blocks; an HMAC-refused policy bundle at cold start makes that org allow-all; the gateway's ReDoS guard silently drops the control plane's "narrowed" backtick rule; one request carries three different action labels.


| **Finding** | **Evidence** | **Consequence** |
|----|----|----|
| Tenants inherit the platform default posture | config_sync.py:687 builds per-org config as {\*\*self.\_config, \*\*data}; get_config falls back to \_config_by_org\["default"\] then the global config (:295-301) | Every key a tenant did not set silently takes the platform value, including enforcement_mode and tier2_enabled. A non-inheriting get_own_config exists at :303-316 and the chat path does not use it. |
| "Unavailable" is indistinguishable from "selected nothing" | PolicySync.is_loaded is true if \*any\* org's bundle is cached (policy_sync.py:228-233) | Org B is reported ready on the strength of org A's bundle, evaluates against an empty rule set, and receives allow. |
| Unset Tier-2 means opposite things on input and output | Input: resolve_tier2_enabled → value is True, default **OFF** (config_sync.py:111-128). Output: \_enabled("output_tier2_enabled", True) → different key, default **ON** (output_guard.py:1010-1015) | tier2_enabled=None disables input scanning and enables output scanning on the same request. |
| /v1/vector/query can never run Tier-2 | \_RAG_GUARDRAIL_KEYS (vector_routes.py:210-215) copies four keys; rag_tier2_enabled is never propagated | A silent policy hole relative to /v1/rag/query, invisible from the console. |
| Same fault, opposite postures | Redis error → rate limiter returns True / allow (rate_limiter.py:116,169,230); circuit breaker returns should_block=True (circuit_breaker.py:397-400) | One outage simultaneously removes rate limiting and blocks all traffic. |
| Streaming BLOCK is a truncation | secure_streaming.py:551-570 emits an error frame after earlier flushes already reached the client | An operator who selected BLOCK received partial delivery. |
| Output guard runs per flush, not once | secure_streaming.py:451-460; header comment at :44-58 records 29 guard passes for 300 token deltas | Output cost scales with chunk count, not response count. |
| Unbounded buffer on the rewrite path | secure_streaming.py:511-513 returns without clearing on every non-DONE rewrite flush | Memory linear in stream length and O(N²) guard cost. |
| Firewall-triggered generation | the output rewrite helper calls LLM_ROUTER.acompletion on the guard's behalf | The firewall makes model calls that are not the customer's request. |
| Raw text to an external model, on by default | llm_judge.py:166 invoke_model, reached from rag_pipeline/query_stage.py:452, default true at main.py:6674, **fails open to regex** | Un-redacted tenant content leaves the platform by default on the RAG path. |

### 10.1.6 Capacity defects the rebuild must not inherit

> **v2.1 CORRECTION (C14, C20).** Measured on a 24-vCPU node: v1 costs 460–631 gateway CPU-ms per request and meets p99 < 20 ms at no rate: its p99 overhead is 1.1 s at 1 RPS (111–206 ms on JSON alone), its highest error-free rate is 25 RPS at a p99 of 6.9–9.0 s, and from 30 RPS it collapses (32.8% timeouts; overload goodput decays to ≈ 15 RPS); with the firewall switched off the proxy path alone still costs 327 ms (69%) and misses 20 ms (JSON p99 239 ms). py-spy: ~30% Starlette BaseHTTPMiddleware/anyio per-chunk disconnect polling, ~27% LiteLLM/OpenAI-SDK/pydantic per-chunk objects, ~16% detection. Gunicorn's shared accept socket with keep-alive clients concentrates load (at 5 RPS three of 18 workers carry ~95%). Under 5× overload there is no shedding: the per-worker Redis pool exhausts and fail-closed checks turn healthy requests into spurious 422 no_provider_configured, 503 circuit_breaker_open and 503 kill_switch_active; audit completeness falls to 0.36 (0.32 under strict matching: stream_complete records carry another request's correlation id because it is read from a context variable at emit time), /health keeps returning 200 while clients fail, self-reported overhead_ms is clamped to 0 for every stream, and disabled stages are audited as executed "allow". nginx without upstream keepalive returns HTTP 502 above ~420 req/s per pair (keepalive: ≥ 30,000 req/s). The synchronous log handler performs 2–14 publishes per request (switching to INFO removes one): 0.14–1.0 ms normally, 66 ms at 5 ms Redis latency.


A genuine cgroup-aware resource detector exists at shared/ai_mesh_shared/resource_budget.py (507 lines) and is well built: cgroup v2 cpu.max/memory.max, cgroup v1 fallbacks, sched_getaffinity, RLIMIT_NOFILE, taking the minimum of all applicable signals.

**It is never imported by gateway runtime Python.** It is invoked only by shell entrypoints, and docker-compose.prod.yml:176 sets WEB_CONCURRENCY: \${WEB_CONCURRENCY:-16} while gateway/entrypoint.sh:17 gives the environment unconditional priority. In production the detector is bypassed entirely and the worker count is the literal 16.

Hard clamps inside the detector itself cap large hosts regardless: asgi_threads clamped to 32 (:326), scanner_pool to 16 (:339), vault_pool to 8 (:346), redis_pool to 256 (:351). Fully hardcoded pools sit outside it: mcp_proxy.py:149 max_connections=64, rate_limiter.py:44 max_connections=100, mcp_oauth.py:477 max_connections=50, and four separate DEFAULT_THREAD_POOL_SIZE constants of 4, 2, 2 and 4 in vector_client.py:20, embedding_vault.py:45, llm_judge.py:25 and context_guard.py:35.

At the edge, deploy/nginx.conf contains **no \`upstream\` block at all**, so no keepalive directive is possible and nginx opens a fresh TCP connection to gateway:8300 for every request. This is a hard connection-churn ceiling that no amount of application tuning removes.

Finally, shared/ai_mesh_shared/redis_log_handler.py attaches a **synchronous** Redis publish to the gateway logger, and main.py:6716-6730 sets that logger to DEBUG. Every log line from every child logger performs a blocking round trip on the event-loop thread.

### 10.1.7 The conclusion, stated plainly

The gateway does not have an architecture that is under-maintained. It has no architecture: there is no layer boundary, no single decision authority, no one place where a request's fate is determined, and no invariant a test could protect. The 5,027-line function is a symptom; the 283 decision sites are the disease.

Repairing this in place would require changing the same code that is simultaneously the only running implementation — which is how the last five review cycles produced the same findings. A clean-room rebuild behind a frozen wire contract is cheaper, because it is the only option in which "done" is checkable.

## 10.2 Replacement doctrine

### 10.2.1 The one contract that is frozen

> **v2.1 CORRECTION (C3, C21).** Two additions. (1) A second customer-visible contract — gateway → control plane → console (telemetry and EnforcementEvent, pipeline_trace, stage timings, the `zeroshield` object and X-ZeroShield-* headers, admin HTTP in both directions, console-written config keys) — is owned by the new card **GW14b**; UI06/UI12 alone would catch its breakage only on GW23's critical path. (2) "Base-URL swap" includes every field of the pinned SDK request and response types, not only messages content: validation found 13 request fields and 4 output channels (logprobs, refusal, reasoning, function_call) that a rebuild scanning "the content" silently leaves open. The executable specification must also genuinely run against the configured application (C10/GW01).


| The only externally visible contract that may not change is this: an application already using the OpenAI SDK must work against AI Mesh by changing base_url and the API key, and nothing else. No custom client, no patched parser, no SDK fork, no header the SDK does not send, no response field the SDK cannot ignore. Every other interface in the backend — internal modules, database schema, trace payload, telemetry shape, config keys, Redis key layout — is explicitly unfrozen and expected to change. |
|----|

This is a narrower contract than "preserve the current behaviour", and deliberately so. It is also a \*stronger\* one, because it is executable: the repository already contains its definition.

gateway/ai_mesh_gateway/tests/test_openai_sdk_compat.py and its five siblings are 1,979 lines and 77 tests, driving the real application through openai==2.38.0 pinned exactly at gateway/pyproject.toml:48. They assert typed parsing of non-streaming completions, streaming chunk parsing, the terminal trace frame's SDK-acceptability, data: \[DONE\] termination, APIStatusError/BadRequestError for blocks, streaming blocks raising \*before\* any SSE byte, AuthenticationError on bad keys, RateLimitError on upstream 429, the /v1/responses typed event sequence, tool-call forwarding and reconstruction, response_format json_schema, usage blocks, embeddings, model listing, legacy /v1/completions, and error-envelope param/code/request_id population across every error class. test_openai_sdk_compat_live_uvicorn.py boots real uvicorn on a loopback TCP port to catch wire-level behaviour that ASGITransport masks.

**This suite is the specification of gateway-v2's public surface.** GW01 lifts it out of the old package, makes it implementation-agnostic, and puts it in CI — where it has never run, because the single existing workflow never installs openai.

### 10.2.2 What the rebuild is allowed to change, and must

> **v2.1 CORRECTION (C14, C21).** Row "Benign inline code: 5/5 backtick prompts hard-blocked at confidence 1.00" is **refuted at the baseline** (live v1 allows 0/5; the control-plane deep scan also 0/5) — drop it. Row "Injection/jailbreak 3/10 same-family, 0/10 paraphrased" is stale (0/10 and 0/10 at the baseline). The table lacks its complement: what v1 does that must be *kept*. Before GW04 build a machine-readable **hardening inventory** of v1's ~95 hardening behaviours (input-channel folding, output-channel scanning, encoding handling, streaming boundary cases) and map each to a v2 test; a behaviour may be dropped only through a pre-registered ledger entry.


Because the fidelity bar is "fix the measured defects, hold equivalence only where measured good", the following behaviours change deliberately and are scored against the detection corpus, not against v1's output:

| **Area** | **v1 behaviour** | **v2 behaviour** | **Scored by** |
|----|----|----|----|
| Injection/jailbreak detection | 3/10 same-family, 0/10 paraphrased | measured against corpus; published recall/FPR per posture | GW02 corpus, expected-diff ledger |
| Benign inline code | 5/5 backtick prompts hard-blocked at confidence 1.00 | injection/command patterns emit **signal**, not terminal block | expected-diff ledger |
| Degraded scan | action becomes independent of verdict | explicit UNAVAILABLE finding status; organization's signed failure posture applies | GW07 |
| Org policy precedence | severity maximum | signed rule priority; org intent is authoritative within its scope | GW07 |
| Unset tri-state | OFF on input, ON on output | one plan, one resolution, identical on every surface | GW05 |
| Missing tenant config | inherits platform default | explicit PLAN_UNAVAILABLE; never another tenant's posture | GW05 |
| Streaming block | truncation after partial delivery | withhold-before-first-byte, or the explicitly selected strict mode | GW12, GW13 |

Every row is a **deliberate, published behaviour change** with its own gate. A parity differ that scored these as regressions would be measuring the wrong thing, which is why §10.7 requires an expected-diff ledger rather than a pass/fail diff.

### 10.2.3 Structural rules for the new package

> **v2.1 CORRECTION (C1, C10, C11, C12, C15).** The gates as implemented do not enforce the rules: shared types have no specified legal home (C1); the HTTP gate misses positional/named status codes, 5xx and raised exceptions and is evaded by import aliases (C11); the capacity-literal gate catches 0 of 14 hidden forms; the mutable-state gate misses 7 forms; the import-linter gate can be disabled silently ("0 kept, 0 broken" exits 0); and nothing blocks a merge — `revamp` is unprotected and required checks are off (C10). Replacement gates: shared types in the lowest layer; an import-linter *forbidden* contract for starlette/fastapi outside `edge/`; a sealed DispatchAuthorization constructor plus a provenance gate (17/17 forgery forms caught); capacity values as NewTypes only `runtime/resources.py` can construct plus the stronger literal gate (15/15 fixtures, 7/7 v1 pools); `raise` banned in `detect/`; a contract-count assertion so a gate cannot pass vacuously; branch protection with these jobs required.


| **Rule** | **Enforcement** |
|----|----|
| Dependencies point one way only, edge → audit. A lower layer may never import a higher one. | import-linter contract in CI; GW00 |
| No module outside resolve/ may produce a terminal security outcome. Detectors return findings; they do not raise HTTP responses, mutate the body, or short-circuit. | Static gate: no HTTPException, JSONResponse or status_code=403 outside edge/ and resolve/; GW07 |
| No function exceeds 120 lines; no module exceeds 800. | CI lint gate; GW00 |
| No capacity value is a literal. Every bound derives from the ResourceContract. | Static gate on numeric literals in pool/queue/worker positions; GW03 |
| One decision record per request phase, emitted from one place. | GW07, GW14 |
| The resolver is pure: no I/O, no clock, no globals. Given the same findings and plan, it returns the same decision. | Property test; GW07 |
| Request-scoped state is immutable after construction. Stages return new values; they do not mutate a shared dict. | Frozen dataclasses; GW04 |
| No module-level mutable state touched per request. | Static gate; GW00 |

The 120-line and 800-line limits are not style preferences. They are the mechanism that prevents proxy_chat from reconstituting itself, and they are checkable by a machine, which matters because this track is executed by agents.

## 10.3 Target backend architecture

### 10.3.1 Layer model

gateway-v2 is a single deployable process — not a microservice fan-out. Splitting auth, policy and scanning across HTTP hops would add round trips to a path with a 20 ms p99 budget. What changes is not the number of processes but the number of \*boundaries inside\* the process, and the direction in which they may be crossed.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>┌──────────────────────────────────────────────────────────────────┐</p>
<p>│ edge/ ASGI, HTTP, SSE framing, OpenAI wire types │</p>
<p>│ the ONLY layer that may construct a response │</p>
<p>└───────────────────────────────┬──────────────────────────────────┘</p>
<p>│ RequestContext (frozen)</p>
<p>┌───────────────────────────────▼──────────────────────────────────┐</p>
<p>│ admit/ identity, tenant, quota lease, kill-switch │</p>
<p>│ bounded shared-state access; 1 round trip │</p>
<p>└───────────────────────────────┬──────────────────────────────────┘</p>
<p>│ Principal + ResourceGrant</p>
<p>┌───────────────────────────────▼──────────────────────────────────┐</p>
<p>│ plan/ pinned ExecutionPlan snapshot for this org │</p>
<p>│ compiled off the hot path, versioned, immutable │</p>
<p>└───────────────────────────────┬──────────────────────────────────┘</p>
<p>│ ExecutionPlan (frozen)</p>
<p>┌───────────────────────────────▼──────────────────────────────────┐</p>
<p>│ detect/ deterministic + semantic detectors │</p>
<p>│ emit Finding[] ONLY — no I/O decisions, no 4xx │</p>
<p>│ guard/ backend interface: CPU, GPU, or remote │</p>
<p>└───────────────────────────────┬──────────────────────────────────┘</p>
<p>│ Finding[]</p>
<p>┌───────────────────────────────▼──────────────────────────────────┐</p>
<p>│ resolve/ THE single authority. Pure function. │</p>
<p>│ (Finding[], ExecutionPlan) -&gt; Decision │</p>
<p>└───────────────────────────────┬──────────────────────────────────┘</p>
<p>│ Decision (frozen)</p>
<p>┌───────────────────────────────▼──────────────────────────────────┐</p>
<p>│ dispatch/ provider client; applies Decision transformations │</p>
<p>│ never originates a model call of its own │</p>
<p>└───────────────────────────────┬──────────────────────────────────┘</p>
<p>│ ProviderStream | ProviderResponse</p>
<p>┌───────────────────────────────▼──────────────────────────────────┐</p>
<p>│ egress/ SSE state machine, bounded buffers, backpressure │</p>
<p>│ output detect → resolve → emit, same types │</p>
<p>└───────────────────────────────┬──────────────────────────────────┘</p>
<p>│ DecisionRecord</p>
<p>┌───────────────────────────────▼──────────────────────────────────┐</p>
<p>│ audit/ async, bounded, lossless-or-counted │</p>
<p>└──────────────────────────────────────────────────────────────────┘</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

Two properties follow mechanically from this shape, and both are the direct answer to a defect in §10.1:

**A finding cannot be lost.** detect/ has exactly one consumer, resolve/, and the type it emits is the type the resolver consumes. There is no adapter in between, so there is no \_scanner_kwargs_for_enforcement able to return all-None and erase a verdict. The adapter existed because the two sides spoke different vocabularies; v2 has one vocabulary.

**A decision cannot be overridden.** resolve/ is the only module permitted to produce a Decision, and egress/ consumes the Decision it is given. There is no second branch that can turn BLOCK into REDACT, because there is no second place where an action is computed.

### 10.3.2 Module tree

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>gateway_v2/</p>
<p>├── edge/</p>
<p>│ ├── app.py ASGI app assembly, lifespan, routers</p>
<p>│ ├── openai_chat.py /v1/chat/completions, /v1/completions</p>
<p>│ ├── openai_responses.py /v1/responses</p>
<p>│ ├── openai_misc.py /v1/embeddings, /v1/models, /v1/moderations</p>
<p>│ ├── mcp.py MCP JSON-RPC + REST surface</p>
<p>│ ├── rag.py /v1/rag/*, /v1/vector/*</p>
<p>│ ├── wire/ OpenAI request/response types, SSE codec</p>
<p>│ └── errors.py the ONLY place an error envelope is built</p>
<p>├── admit/</p>
<p>│ ├── identity.py key → Principal, with epoch-based revocation</p>
<p>│ ├── quota.py local GCRA + shared lease</p>
<p>│ ├── killswitch.py snapshot + fail-closed refresh</p>
<p>│ └── grant.py ResourceGrant: what this request may consume</p>
<p>├── plan/</p>
<p>│ ├── model.py ExecutionPlan, Rule, Mode, Action, FailurePosture</p>
<p>│ ├── compiler.py validate + compile (control plane, off hot path)</p>
<p>│ ├── store.py versioned snapshot distribution</p>
<p>│ └── snapshot.py per-replica immutable cache + freshness age</p>
<p>├── detect/</p>
<p>│ ├── base.py Detector protocol, Finding, FindingStatus</p>
<p>│ ├── deterministic/ PII, secrets, credentials, transport, size</p>
<p>│ ├── semantic/ injection/jailbreak via guard backend</p>
<p>│ ├── windowing.py tokenizer-aware segmentation + FPR accounting</p>
<p>│ └── guard/</p>
<p>│ ├── backend.py GuardBackend protocol</p>
<p>│ ├── local_onnx.py CPU dev/default</p>
<p>│ ├── local_trt.py in-process TensorRT</p>
<p>│ ├── triton_grpc.py node-local Triton</p>
<p>│ └── remote_http.py off-box burst</p>
<p>├── resolve/</p>
<p>│ ├── resolver.py PURE: (Finding[], ExecutionPlan) -&gt; Decision</p>
<p>│ ├── decision.py Decision, Disposition, Transformation</p>
<p>│ └── conflict.py signed rule priority, no severity max</p>
<p>├── dispatch/</p>
<p>│ ├── provider.py ProviderClient protocol</p>
<p>│ ├── routing.py deterministic selection from the plan</p>
<p>│ └── transform.py applies Decision.transformations, verifies bytes</p>
<p>├── egress/</p>
<p>│ ├── stream.py SSE state machine + bounded coalescer</p>
<p>│ ├── backpressure.py credit-based flow control</p>
<p>│ ├── output_guard.py output detect → resolve → emit</p>
<p>│ └── strict.py whole-response withhold mode</p>
<p>├── audit/</p>
<p>│ ├── record.py DecisionRecord (one per phase)</p>
<p>│ ├── sink.py bounded async producer</p>
<p>│ └── metrics.py Prometheus collectors + timing instrument</p>
<p>├── runtime/</p>
<p>│ ├── resources.py ResourceContract (see §10.6)</p>
<p>│ ├── lifecycle.py startup, readiness, drain</p>
<p>│ └── clock.py injectable time source</p>
<p>└── contracts/</p>
<p>└── openai_conformance/ the frozen suite, implementation-agnostic</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

### 10.3.3 The dependency contract, enforced

> **v2.1 CORRECTION (C1).** The layer order is sound; the interface placement in §10.3.2/§10.5 is not. With Finding in detect/base.py, ExecutionPlan in plan/model.py, Decision in resolve/decision.py and egress calling detect and resolve, the declared contract reports 12 illegal layer pairs over 17 imports (executed). Fix, proven independently twice: every shared value type and protocol lives in the lowest layer (`contracts/`, or a new bottom `domain/`), `edge/` injects the detect/resolve callables that egress and dispatch need, and a second contract states that `resolve/` imports only that layer — lint-imports KEPT with the identical layer list, mypy --strict clean, LGW04-3 and the LGW00-3 negative fixture still fail as required. The prototype (4,651 lines, 49 modules) was built this way and passes the repository's GW00 gates.


The layer order is edge \> admit \> plan \> detect \> resolve \> dispatch \> egress \> audit \> runtime \> contracts. A module may import from strictly lower layers and from runtime/contracts. It may never import upward or sideways.

This is declared in pyproject.toml as import-linter layers and checked in CI. It is the single most important gate in the track, because it is what prevents the 283 decision sites from re-forming: detect/ cannot import edge/, so a detector \*cannot\* return a 403 even if someone tries.

### 10.3.4 What is deliberately not in the architecture

| **Excluded** | **Why** |
|----|----|
| Separate auth/policy/scan microservices | Each hop costs a round trip against a 20 ms p99 budget. TrueFoundry's published 3–12 ms hop is in-process for exactly this reason. |
| An ORM on the request path | dispatch/ and admit/ open zero database connections. Control-plane state arrives as compiled snapshots. |
| A message broker in front of the model call | Acking before generation means the chat is not complete; acking after adds a hop to the same wait. |
| Any platform-owned external AI | §1's locked position. The GuardBackend protocol has no remote-model implementation that is not the tenant's own. |
| A firewall-initiated generation call | REWRITE, if supported, uses a local model through GuardBackend, never dispatch/. |
| Framework middleware for security | Four BaseHTTPMiddleware layers measured at 1.00 ms p50 / 4.70 p99. v2 uses pure-ASGI middleware and does security work in admit/, where it is timed. |

## 10.4 The request lifecycle

> **v2.1 CORRECTION (C11, C12).** "BLOCK short-circuits structurally … by type" is not fully enforceable in Python: a sealed DispatchAuthorization constructor stops direct construction and `dataclasses.replace`, but `object.__new__`+setattr and relabelling a Decision before minting still reach a recording provider. Keep the design; enforce it with the provenance gate + a runtime assertion at the provider entry point + recorder-verified zero calls. Steps ② and ③ produce terminal outcomes (401/429/503, kill-switch, PLAN_UNAVAILABLE) outside resolve/: they are an ADMISSION phase whose facts map to outcomes through one posture table implemented once; only `edge/` renders responses.


<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>client (OpenAI SDK)</p>
<p>│ POST /v1/chat/completions</p>
<p>▼</p>
<p>① edge/ t0 ← clock.now() ← the timer starts HERE, before identity</p>
<p>│ parse wire types, build frozen RequestContext</p>
<p>▼</p>
<p>② admit/ identity → Principal (RAM cache; shared store only on miss)</p>
<p>│ kill-switch snapshot (RAM, age-bounded, fail-closed on stale)</p>
<p>│ quota: local GCRA + one shared lease op</p>
<p>│ → ResourceGrant | reject: 401 / 429 / 503</p>
<p>▼</p>
<p>③ plan/ pin ExecutionPlan for this org, by version</p>
<p>│ PLAN_UNAVAILABLE is a distinct outcome, never "no rules"</p>
<p>▼</p>
<p>④ detect/ plan-selected input detectors run</p>
<p>│ deterministic detectors: always, in-process</p>
<p>│ semantic detectors: only if the plan selects one</p>
<p>│ each returns Finding[] with status EXECUTED | SKIPPED | UNAVAILABLE</p>
<p>▼</p>
<p>⑤ resolve/ Decision = resolve(findings, plan) ← PURE</p>
<p>│ disposition ∈ ALLOW | FLAG | REDACT | BLOCK</p>
<p>│ + transformations[] + provenance</p>
<p>▼</p>
<p>BLOCK ──────────────────────────────► ⑧ (zero provider calls, by construction)</p>
<p>│</p>
<p>⑥ dispatch/ apply transformations, VERIFY transformed bytes,</p>
<p>│ then one provider call with the verified payload</p>
<p>▼</p>
<p>⑦ egress/ per released chunk: output detectors → resolve → emit</p>
<p>│ bounded coalescer, credit-based backpressure</p>
<p>│ cancellation propagates to provider and guard</p>
<p>▼</p>
<p>⑧ audit/ exactly one DecisionRecord per phase, enqueued, never awaited</p>
<p>│</p>
<p>▼</p>
<p>client</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

Three things in this sequence are load-bearing and each replaces a named defect:

**The timer starts in \`edge/\` at ①, before identity resolution.** In v1, start = time.perf_counter() sits at main.py:7085 \*inside\* the handler, after AuthMiddleware has already performed its Redis round trip — so authentication latency is invisible to every trace, and stage_metrics\["auth_ms"\] at :7691 actually measures body parsing. §1.2's T_fw_addon cannot be measured honestly from a timer that starts after the work begins.

**\`model_output_ms\` is measured, never derived.** v1 computes it by subtraction (stream_orchestration.py:683: duration_ms - ttft_ms, explicitly excluding TTFT), so provider time-to-first-token lands in overhead_ms and is reported as firewall overhead. v2 timestamps provider-stream open and close directly. The reconciliation residual \|wall − Σstages\| is recorded **signed**, and a non-zero p50 fails CI.

**BLOCK short-circuits structurally, not by convention.** dispatch/ is only reachable from a Decision whose disposition permits dispatch. There is no code path in which a blocked request can reach a provider, because the provider client's entry point requires a DispatchAuthorization value that resolve/ only mints for non-blocking dispositions.

## 10.5 Core interface specifications

These are the contracts agents implement against. They are deliberately small.

### 10.5.1 Findings — what detectors say

> **v2.1 CORRECTION (C21).** A Finding's spans must be addressable across the whole request, not per string: detectors scan (a) every text-bearing field of the pinned SDK request types — messages content and parts, name, tool and function definitions and their parameter descriptions, tool-call and legacy function-call arguments (decoded JSON string values, span-mapped back), response_format schemas, prediction, metadata, user, image/audio/file references — and (b) the joined text of each message's parts, so a value split across parts is detected. Unscannable parts (image, audio, file bytes) need an explicit plan posture (reject, or allow with a SKIPPED finding) and must never be reported as EXECUTED. A CI gate fails when a field of the pinned SDK types carries text but has no scanner mapping.


<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>class FindingStatus(StrEnum):</p>
<p>EXECUTED = "executed" # the detector ran and reports this result</p>
<p>SKIPPED = "skipped" # the plan did not select it; no work was done</p>
<p>UNAVAILABLE = "unavailable" # selected, but could not run (model down, timeout)</p>
<p>@dataclass(frozen=True, slots=True)</p>
<p>class Finding:</p>
<p>detector: str # stable id, e.g. "pii.email"</p>
<p>detector_version: str # model or ruleset hash — in the audit record</p>
<p>category: str # taxonomy term, ONE spelling, from plan/model.py</p>
<p>status: FindingStatus</p>
<p>confidence: float | None # None iff status is not EXECUTED</p>
<p>spans: tuple[Span, ...] # byte offsets into the scanned text</p>
<p>evidence: str | None # redacted, bounded</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

A detector returns Finding\[\] and nothing else. It cannot block, cannot mutate, cannot log a decision, cannot raise an HTTP error. UNAVAILABLE is a first-class result: it is never coerced to a clean pass, which is the defect at §10.1.2 removed by construction.

category is drawn from a single enum in plan/model.py. The injection vs prompt_injection divergence in §10.1.3 cannot recur, because there is one spelling and it fails to compile otherwise.

### 10.5.2 The execution plan — what the organization decided

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>@dataclass(frozen=True, slots=True)</p>
<p>class Rule:</p>
<p>rule_id: str</p>
<p>category: Category</p>
<p>mode: Mode # OFF | MONITOR | ENFORCE</p>
<p>action: Action # ALLOW | FLAG | REDACT | BLOCK | REWRITE</p>
<p>threshold: float | None</p>
<p>priority: int # explicit; conflicts resolve by this, never by severity</p>
<p>scope: RuleScope # INPUT | OUTPUT | BOTH, and which surfaces</p>
<p>on_unavailable: FailurePosture # FAIL_OPEN | FAIL_CLOSED | DEGRADE_TO(action)</p>
<p>@dataclass(frozen=True, slots=True)</p>
<p>class ExecutionPlan:</p>
<p>org_id: str</p>
<p>version: str # monotonic; appears in every audit record</p>
<p>compiled_at: float</p>
<p>rules: tuple[Rule, ...]</p>
<p>required_detectors: frozenset[str] # derived at compile time</p>
<p>streaming_mode: StreamingMode # INCREMENTAL | STRICT_WITHHOLD</p>
<p>integrity: PlatformIntegrity # NOT tenant-editable</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

The plan is compiled by the control plane, validated once, versioned, and distributed. The hot path never interprets raw configuration, never reads a tri-state, and never applies a default — those decisions happened at compile time where they can be validated and rejected.

required_detectors is derived: if no enabled rule needs the semantic model, the model is not invoked, and that is a property of the compiled plan rather than a runtime if. This is what makes OFF genuinely mean "no work was done for that rule".

Three plan states are distinct and must stay distinct:

| **State** | **Meaning** | **Hot-path behaviour** |
|----|----|----|
| ExecutionPlan with rules=() | The organization is authorized and selected no optional content rules. | Platform integrity applies; no content rules run. |
| PLAN_UNAVAILABLE | The plan could not be loaded, is stale beyond its freshness bound, or failed validation. | Signed not-ready behaviour. **Never** another tenant's plan, never the platform default. |
| PLAN_UNKNOWN_TENANT | The principal resolved but has no plan. | Explicit onboarding error. Never inherited defaults. |

§10.1.5's inheritance defect is a direct consequence of v1 collapsing all three into one dictionary lookup with a fallback chain.

### 10.5.3 The resolver — the single authority

> **v2.1 CORRECTION (C22 — CRITICAL).** "Resolve candidate conflicts by explicit priority … no severity maximum" is unsafe across *different* findings: in the prototype, a PII REDACT rule given higher priority than a secrets BLOCK rule sent the AWS key to the provider, and at equal priority renaming a rule flipped the outcome. Replace step 5: each finding is resolved under the rules that select *its* detector (priority orders only rules matching the same finding, which is where organisation intent overrides a recommendation — the fix for P1); the request's disposition is the most restrictive per-finding disposition (BLOCK > REDACT > FLAG > ALLOW), so an organisation's ALLOW for category X never cancels a BLOCK for category Y; tie-breaks never depend on names. Add properties: adding a REDACT or FLAG rule never removes a BLOCK; renaming rules never changes a disposition. GW05's compiler rejects plans whose semantics the resolver cannot represent.


<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>def resolve(</p>
<p>findings: Sequence[Finding],</p>
<p>plan: ExecutionPlan,</p>
<p>phase: Phase, # INPUT | OUTPUT</p>
<p>) -&gt; Decision:</p>
<p>"""Pure. No I/O, no clock, no globals, no logging side effects.</p>
<p>Same inputs → same Decision, always."""</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>@dataclass(frozen=True, slots=True)</p>
<p>class Decision:</p>
<p>disposition: Disposition # ALLOW | FLAG | REDACT | BLOCK</p>
<p>transformations: tuple[Transformation, ...]</p>
<p>findings: tuple[Finding, ...] # ALL of them, including SKIPPED</p>
<p>plan_version: str</p>
<p>deciding_rules: tuple[str, ...] # which rule_ids produced this, in priority order</p>
<p>unavailable_detectors: tuple[str, ...]</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

Resolution order, and it is total:

222. Partition findings by the rule that selected the detector. A finding with no selecting rule is recorded and has **no** effect on disposition.

223. Drop SKIPPED. Route UNAVAILABLE through that rule's on_unavailable posture — never through a generic degraded branch.

224. For MONITOR rules, record the finding and contribute **nothing** to disposition.

225. For ENFORCE rules, the rule's configured action is the candidate.

226. Resolve candidate conflicts by explicit priority, then by a documented tie-break. **There is no severity maximum.**

227. BLOCK is terminal: no later stage may alter it.

Because the function is pure, it is property-testable: the track requires a Hypothesis suite asserting that no (findings, plan) pair produces a disposition not derivable from an ENFORCE rule, and that adding a MONITOR rule never changes a disposition. Both properties are false in v1 today, as P1, P6 and P7 demonstrate.

### 10.5.4 The guard backend — pluggable, no topology assumed

> **v2.1 CORRECTION (C7, C23, C30, C35).** Measured on L4: exactly one process owns each GPU (exact-shape 1×512 fp16 TensorRT engine, batch 1, FIFO, `disable_fallback()`, TensorRT asserted first at readiness); micro-batching and per-worker sessions make latency and throughput worse. The owner enforces admission **per tenant** (weighted fair queuing; a tenant-blind FIFO let one tenant's 16-window prompt shed another tenant 30/30) and sheds with `x-should-retry: false` or an adequate Retry-After. Workers reach the owner over a Unix socket (node-local) or TCP (off-box; same-zone network ≈ 0.1 ms); frames carry the remaining budget, not a timestamp. Readiness requires recent successful guard replies (a stuck inference thread otherwise keeps readiness green while FAIL_OPEN tenants run unscanned). `capacity_hint()` reports the owner's measured service rate. Engine caches are keyed by model, TensorRT and driver version. Tokenization (~3 µs/token on G2, ~1.7 µs/token on C4) belongs to the gateway's CPU budget. PG2-86M cannot meet a 20 ms budget for ≤ 1,024-token prompts.


<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>class GuardBackend(Protocol):</p>
<p>async def classify(self, windows: Sequence[str], budget: Budget) -&gt; Sequence[GuardResult]: ...</p>
<p>async def readiness(self) -&gt; Readiness: ... # includes model_hash</p>
<p>def capacity_hint(self) -&gt; CapacityHint: ... # windows/s, queue depth, batch profile</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

detect/semantic/ calls this and knows nothing about CPU, GPU, TensorRT, Triton, batch size or replica count. The selection is deployment configuration. This is precisely what "no capacity assumption tied to the present budget" means in code: moving from one L4 to forty is a config change and a capacity_hint change, not a source edit.

readiness() returning not-ready means selected semantic rules are UNAVAILABLE, which routes through on_unavailable. It never means "clean".

### 10.5.5 The egress pipeline

> **v2.1 CORRECTION (C4, C21, C25).** Record T_release_processing and T_holdback_wait separately per chunk. Hold back only when an enforcing output rule exists, only for the pattern classes it selects, and only the minimal suffix that could still become a match; bytes scanned per byte released must be O(1) (the prototype's rescanning holdback cost 17.9 s CPU for 32 KiB of base64 and held a 64 KiB run for the whole stream); a bounded held-byte ceiling has a declared outcome. Every text-bearing output field is scanned — content, refusal, reasoning, tool and function arguments (decoded), audio transcript, **logprobs tokens and bytes**, annotations — or the plan rejects the request parameter that exposes an unscannable channel (e.g. `logprobs`, `n > 1`, `audio`) when an output REDACT rule is active. Output text is canonicalized before matching. Structured outputs (json_object, json_schema, tool arguments) must still validate after redaction, or the unmaskable posture fires. Provider error frames are scanned like content.


<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>class StreamPipeline:</p>
<p>async def run(self, upstream: ProviderStream, decision_ctx: OutputContext) -&gt; AsyncIterator[bytes]:</p>
<p>"""Bounded. Cancellable. Backpressure-aware.</p>
<p>Buffer high-water mark derives from ResourceContract, never a literal."""</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

Requirements, each a named v1 defect:

| **Requirement** | **Replaces** |
|----|----|
| A single bounded coalescer; buffer ceiling from ResourceContract, not 4096 | unbounded rewrite-path buffer, secure_streaming.py:511-513 |
| Output detectors run on a **content-defined schedule**, not per flush; per-request invocation count is a recorded metric with a plan-derived ceiling | 29 guard passes per 300 deltas |
| Cancellation propagates to provider and guard within a bounded interval | polled is_disconnected that ends the generator but never aborts upstream |
| BLOCK in INCREMENTAL mode is only expressible before the first content byte; after that the only truthful outcomes are terminate-with-error or the tenant's selected STRICT_WITHHOLD | truncation-presented-as-block |
| Backpressure is credit-based; a slow consumer slows upstream reads rather than growing memory | no backpressure at all |

The third row is a product statement, not an implementation detail: once bytes have left, they cannot be unsent. v2 makes the tenant choose the mode up front and then tells the truth about which one they got.

## 10.6 Capacity from the environment

### 10.6.1 The contract

> **v2.1 CORRECTION (C10 — GW03).** The implemented contract hides capacity assumptions in defaults: per-worker RSS 400 MiB (an in-process 86M worker measured 6.77 GB and was OOM-killed; a remote-guard worker 0.32 GB), utilisation 0.75 and p99 20 ms. queue_depth is independent of the p99 target (8 at 20, 200 and 2,000 ms); the provider pool equals the worker count and the Redis/connection pools equal the whole fd budget (786,418 per worker on a G2 unit, so they bound nothing); the process refuses to start at 1–1.3 vCPU (T18 L18-1 becomes impossible); limits set on parent cgroups are ignored (12 workers derived where the truth is 1); a shrinking SIGHUP crashes; guard owner processes are not modelled; and pool_size(GUARD) shared across workers gives each worker ≈ 1 window — the cap that sets the prototype's measured single-unit knee while the GPU is 15% busy. Per-worker RSS and service rates must be measured at warm-up, the owner must be modelled, and guard admission must be bounded at the owner, not per worker. The per-worker share also behaves as a loss queue: with Poisson arrivals it shed 3.5% at 78 RPS, and < 0.1% sheds would need ≈ 2.2–2.6 RPS per g2-standard-24.


<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr>
<th><p>@dataclass(frozen=True, slots=True)</p>
<p>class ResourceContract:</p>
<p>cpu_quota: float # cgroup v2 cpu.max → v1 cfs_quota → sched_getaffinity</p>
<p>memory_limit: int # cgroup memory.max → v1 limit → /proc/meminfo</p>
<p>fd_limit: int # RLIMIT_NOFILE</p>
<p>guard_capacity: CapacityHint | None # from GuardBackend.capacity_hint()</p>
<p>target_p99_ms: float # deployment-declared SLO, not a constant</p>
<p>utilization_cap: float # deployment-declared headroom</p>
<p>def workers(self) -&gt; int: ...</p>
<p>def queue_depth(self, service_rate: float) -&gt; int: ...</p>
<p>def pool_size(self, kind: PoolKind) -&gt; int: ...</p>
<p>def stream_buffer_bytes(self, active_streams: int) -&gt; int: ...</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

Every bound in the process is a method call on this object. runtime/resources.py is the **only** module permitted to contain a numeric literal in a capacity position, and the CI gate at GW03 enforces that by AST inspection of pool/queue/worker constructor arguments across the package.

### 10.6.2 What this replaces, specifically

The detector at shared/ai_mesh_shared/resource_budget.py is good work and its \*detection\* half is carried forward — the cgroup v2 → v1 → affinity → RLIMIT chain with minimum-of-signals is correct and tested. Three things change:

228. **It is imported in-process**, not shelled out from an entrypoint. Pools can then resize on a SIGHUP without a container restart.

229. **The clamps are removed.** asgi_threads capped at 32, scanner_pool at 16, vault_pool at 8, redis_pool at 256 are exactly the "capacity assumption in code" the objective forbids. Bounds become functions of measured service rate and the declared p99 target.

230. **\`WEB_CONCURRENCY\` stops overriding it.** docker-compose.prod.yml:176 and the unconditional env priority at gateway/entrypoint.sh:17 are removed; an explicit override remains available but is logged at startup as a deviation, with the detected value alongside it.

The seven hardcoded pools listed in §10.1.6 — mcp_proxy.py:149, rate_limiter.py:44, mcp_oauth.py:477, and the four DEFAULT_THREAD_POOL_SIZE constants — have no counterpart in v2; those call sites take a ResourceContract and ask it.

### 10.6.3 Scale-out as a deployment action

The §7 table remains the contract. What §10.6 adds is the mechanism that makes it true: because every bound is derived and every fleet boundary is an interface (GuardBackend, ProviderClient, StateStore, AuditSink), moving from four nodes to forty changes replica counts and a capacity_hint. There is no source file in which the number of GPUs, the number of workers, or the target RPS appears.

## 10.7 Parity and cutover machinery

Greenfield-parallel with a single cutover has one failure mode: the new system is beautiful and wrong in a way nobody noticed. The entire risk budget of this track goes into making that detectable before the cutover, not after.

### 10.7.1 The three-corpus method

> **v2.1 CORRECTION (C8, C31).** Production has no traffic, so C2 cannot be a capture. C2 becomes a declared, hashed synthetic distribution with a published generator: per surface × mode × tenant cell at least N distinct shapes; multi-turn conversations with resent history; multi-chunk streams with realistic pacing and realistic output shapes (URLs, UUIDs, digits, code, base64 — not a 119-word dictionary); non-ASCII and code-mixed input; fragmented tool calls; format-preserving PII/secrets (placeholders never exercise REDACT); every SDK text field (C21); fault injections; ≥ 10k tenants. The implemented C2 (50,000 records = 40 distinct requests × 1,250; replay never calls v1) does not satisfy GW02.


| **Corpus** | **Source** | **Size target** | **Purpose** |
|----|----|----|----|
| **C1 — wire conformance** | the existing 77 SDK conformance tests, lifted and made implementation-agnostic | 77 tests, grown as gaps appear | v2's public contract. Must pass 100%, always. |
| **C2 — recorded production shape** | sanitized capture from staging/production-shaped traffic across all four surfaces | ≥50,000 requests, all surfaces, both streaming modes | Replay corpus for the differ. Establishes that v2 handles the shapes that actually occur. |
| **C3 — labelled detection** | §4's T04 corpus: ≥300 injections across ≥8 families incl. a disjoint-vocabulary paraphrase family, ≥300 benign incl. a developer-traffic family | as T04 | Scores the deliberate behaviour changes. **Neither v1 nor v2 is the oracle; the labels are.** |

C3 is what makes "fix the defects" executable. Without it, v2's improved injection recall is indistinguishable from a regression, and its refusal to block backticks looks like a hole.

### 10.7.2 The differ and the expected-diff ledger

> **v2.1 CORRECTION (C8).** A git-timestamp comparison cannot prove pre-registration: a post-hoc entry with back-dated author and committer dates was accepted under every wiring. Pre-registration is proven by ancestry: the commit that introduced an entry must be an ancestor of the commit at which the first run observed its signature (CI keeps an append-only log of run → HEAD → signatures). Add a CONSOLE bucket for differences visible to the console but not the SDK (C3).


Replaying C2 through v1 and v2 produces a diff per request. Every diff lands in exactly one bucket:

| **Bucket** | **Meaning** | **Gate** |
|----|----|----|
| **IDENTICAL** | Same disposition, same transformations, same provider bytes. | Target for the majority of C2. |
| **EXPECTED** | Matches a pre-registered entry in the expected-diff ledger, with a rule id, the §10.2.2 row it implements, and its C3 score. | Allowed, counted, reviewed. |
| **UNEXPECTED** | Everything else. | **Blocks cutover. No exceptions.** |
| **WIRE** | Any difference visible to the OpenAI SDK. | **Blocks cutover.** C1 should have caught it; a WIRE diff means C1 has a gap and C1 gets extended. |

The ledger is written \*before\* the differ runs, as part of the task that changes the behaviour. An entry added after a diff is observed is a post-hoc rationalization and the GW21 gate rejects ledger entries whose git timestamp is later than the diff run.

### 10.7.3 Shadow execution

> **v2.1 CORRECTION (C8, C9).** "≥ 72 hours and ≥ 5 million requests" of live-shaped traffic needs 19.3 RPS of traffic that does not exist. Against a one-chunk recorder, shadow cannot validate the output path, streaming modes, wire parity or resource curves. Shadow becomes a long synthetic replay of C2 through both versions with a streaming recorder, plus — once any tenant exists — a tee of real provider responses into v2. Shadow admission runs against a namespaced store so it cannot consume real quota.


v2 runs in shadow against live-shaped traffic before it serves any: v1 serves the response; v2 receives a copy of the request, executes fully against the synthetic provider recorder, and its decision is recorded, never delivered. Shadow must reach a stable window — the track uses ≥72 hours and ≥5 million requests with zero UNEXPECTED diffs — before the cutover rehearsal is scheduled.

Shadow also measures what a replay cannot: real concurrency, real tenant mix, real plan-update-under-load, and v2's actual resource curve beside v1's.

### 10.7.4 Why big-bang, and what makes it survivable

> **v2.1 CORRECTION (C9).** The objection applies to per-request splitting only. A per-tenant cohort cutover (every tenant served wholly by v1 or v2) avoids it: simulated over 200 tenants × 40 requests, 0/200 tenants saw inconsistent dispositions under cohorts vs 43/130/158 of 200 at 5/25/50% per-request splits. With no production traffic today the cutover is a deploy plus an edge switch; the first real tenants move in cohorts, with T24 (real-provider canary on v2) as cohort 1 before GW23.


A single cutover was chosen over route-by-route because the two systems disagree deliberately (§10.2.2) — and running both simultaneously on live traffic would mean the same tenant gets different security dispositions depending on which gateway served the request. That is worse than either system alone, and it is unauditable.

What makes one cutover survivable is that it is a **routing change, not a deployment**: v2 is already deployed, already warm, already shadowing, and the switch is an edge weight. Rollback is the same weight moved back, with v1 still running and still warm. The rehearsal in GW22 executes the full sequence — cut, observe, roll back — on staging under load, and the measured rollback time becomes the published number.

## 10.8 Backend rebuild task index

> **v2.1 CORRECTION (C2).** The "Depends on" column below, the sequencing prose ("everything else is a strict chain") and the §10.13 lanes disagree, and 28 acceptance tests need artifacts from cards that run later. §0.5 (Part 0) is the single authoritative table: it adds GW14b and GW16b, moves T24 before GW23 and relocates the forward-reference tests. Parallel lanes after GW00: GW01 → GW02 (needs T02, T04); GW03; GW04 → GW05 → GW06/GW07; GW08 → GW09/GW10. The lanes converge at GW15; GW16–GW18 rejoin at GW20.


| **Task** | **Title** | **Phase** | **Depends on** |
|----|----|----|----|
| GW00 | Re-pin the baseline, resolve the credential exposure, stand up the v2 workspace and dependency gates | Foundation | — |
| GW01 | Freeze the OpenAI wire contract as an implementation-agnostic conformance suite in CI | Foundation | GW00 |
| GW02 | Build the parity harness: recorded corpus, replay differ, expected-diff ledger | Foundation | GW00, GW01 |
| GW03 | Implement the ResourceContract and prohibit capacity literals | Foundation | GW00 |
| GW04 | Define the core domain types and prove request state is immutable | Core | GW00 |
| GW05 | Build the plan compiler, distribution and snapshot with three distinct plan states | Core | GW04 |
| GW06 | Build the admission layer with bounded shared state and one round trip | Core | GW03, GW04 |
| GW07 | Build the single pure resolver and prove nothing else can decide | Core | GW04, GW05 |
| GW08 | Build the detector framework and the GuardBackend interface | Detection | GW04, GW07 |
| GW09 | Implement deterministic detectors with byte-verified redaction | Detection | GW08 |
| GW10 | Implement semantic detection, windowing and the per-request FPR budget | Detection | GW08 |
| GW11 | Build provider dispatch with verified transformation and no firewall-initiated generation | Dispatch | GW07 |
| GW12 | Build the SSE egress pipeline with bounded buffers, backpressure and real cancellation | Protocol | GW03, GW11 |
| GW13 | Implement output enforcement and the two explicit streaming modes | Protocol | GW07, GW12 |
| GW14 | Build async bounded audit and the honest timing instrument | Observability | GW04, GW12 |
| GW15 | Ship the chat surface on v2 and pass the full conformance suite | Surface | GW01, GW11, GW13, GW14 |
| GW16 | Ship embeddings, models, moderations and legacy completions | Surface | GW15 |
| GW17 | Ship the MCP surface on the same plan and resolver | Surface | GW15 |
| GW18 | Ship RAG and vector surfaces on the same plan and resolver | Surface | GW15 |
| GW19 | Implement admission control, overload semantics and graceful drain | Hardening | GW06, GW12 |
| GW20 | Measure one v2 serving unit and prove horizontal scaling | Performance | GW15–GW19 |
| GW21 | Run shadow execution to ledger closure | Migration | GW20, GW02 |
| GW22 | Rehearse cutover and rollback under load | Migration | GW21 |
| GW23 | Execute the production cutover | Migration | GW22 |
| GW24 | Decommission v1, remove external-AI paths and credentials, publish the evidence pack | Release | GW23 |

Parallelism for agent execution: GW01, GW02 and GW03 are independent of each other and of GW04. GW09 and GW10 are independent. GW16, GW17 and GW18 are independent once GW15 lands. Everything else is a strict chain.

## 10.9 Backend rebuild task cards

## GW00 — Re-pin the baseline, resolve the credential exposure, stand up the v2 workspace and dependency gates

> **v2.1 CORRECTION (C10) — re-open.** (1) The pinned baseline 2a657fad cannot serve chat — re-pin to revamp HEAD correlated to the deployed image digest. (2) No gate blocks anything: `revamp` is unprotected, required checks are off, all 11 GW runs were direct pushes, commit 4e74c573 landed red, and the image digest is uploaded but never compared — enable branch protection with the gate jobs required and compare digests. (3) The installed .git/hooks pre-commit is the pre-GW00 copy (passes an extensionless key) — install via a tracked hooks path. (4) The secret scan misses PKCS#8 "BEGIN PRIVATE KEY", suffixed files, dotfiles and history. (5) The import-linter gate passes vacuously if its contract disappears — assert the contract count. (6) LGW00-6: OCI manifest digests differ between hosts. (7) Adopt the C1 type placement and the C10/C11 gates.


| **Field** | **Value** |
|----|----|
| Phase | Foundation |
| Depends on | None |
| Primary owner | Backend lead + security owner |
| Objective | Establish which commit is truth, remove a live credential exposure, and create a workspace whose structural rules are machine-enforced from the first commit. |

### Why this task exists

Three branches disagree and the newest by commit time is not the one previously audited: main @ 6cec3e28 (stale, 2026-08-10), ansh @ 2a657fad (2026-09-17 12:35), revamp @ 877c27a8 (2026-09-17 13:39, carries this runbook, the V3 runbook and staging/t02/). Separately, ai-mesh-firewall at the repository root is a tracked **OpenSSH private key** — verified header, ssh-ed25519 per the adjacent .pub, 399 bytes — present on **all three branches**. .gitignore covers \*.pem, \*.key, \*.secret but the file has no extension, so no pattern matched it.

### Implementation work

Classify the key privately. Treat it as compromised: revoke the authorization it grants and rotate anything reachable by it **before** any new staging credential is issued. Open a history-rewrite ticket separately; deleting the file does not revoke access. Add an extensionless-private-key rule to .gitignore and to scripts/ralph/precommit-secret-scan.sh, and add a CI job that greps every tracked file for PEM and OpenSSH headers.

Diff the three branches and record which is truth, which images production actually runs, and the rollback tuple. Publish the decision; every later task cites this SHA.

Create gateway_v2/ as a new package with the §10.3.2 tree, empty modules, and the gates live from commit one: import-linter layer contract; function-length ≤120 and module-length ≤800 lints; an AST gate rejecting HTTPException, JSONResponse and status_code=4xx outside edge/ and resolve/; an AST gate rejecting module-level mutable state; ruff and mypy --strict.

Wire the CI workflow that will gate this track. The existing .github/workflows/chat-pipeline-golden.yml runs two test files on six paths and never installs openai; the new workflow runs the full v2 suite plus the conformance suite on every PR.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW00-1 | Attempt authentication with the exposed key against every system it could reach. | Access denied everywhere; rotation record signed by the infrastructure owner. | Any system still accepts it. |
| LGW00-2 | Commit a file containing an OpenSSH private-key header with no extension; push to a branch. | Pre-commit blocks locally; CI fails the PR. | Either gate passes it. |
| LGW00-3 | Add from gateway_v2.edge import app inside gateway_v2/detect/base.py; run CI. | import-linter fails with the violated contract named. | CI green. |
| LGW00-4 | Add a 130-line function and an 850-line module; run CI. | Both lints fail with file and line. | Either passes. |
| LGW00-5 | Add raise HTTPException(403) inside gateway_v2/detect/; run CI. | AST gate fails naming the file. | CI green. |
| LGW00-6 | Build the v2 image from the pinned SHA twice on different hosts. | Byte-identical digests. | Digests differ; the build is not reproducible. |

### Failure scenarios that must be handled

Key is genuine and still authorized → revoke before anything else, and treat every credential in the same blast radius as rotated. Branch truth is contested → do not proceed on assumption; the deployed image digest decides. Reproducible build fails → fix the build before any measurement, because every later number is attributed to a SHA.

### Exit criteria

Credential classified and, if genuine, revoked and rotated with a signed record. Baseline SHA published. gateway_v2/ exists, empty, with all five structural gates demonstrably failing on deliberately bad input and passing on the empty tree. CI workflow runs on every PR.

### Evidence package

Rotation record · branch diff and deployed-image correlation · CI run showing each gate failing on its negative fixture · two identical image digests · pinned SHA in the runbook header.

### Rollback / stop rule

No rollback — nothing is deployed. **Stop** if the key cannot be classified within one working day: escalate rather than proceed, because every subsequent staging credential is issued into a possibly-compromised perimeter.

### Agent execution notes

Fully agent-executable except the credential classification and revocation, which require a human with infrastructure authority. Agents must not print key material into logs, PR descriptions or task output.

## GW01 — Freeze the OpenAI wire contract as an implementation-agnostic conformance suite in CI

> **v2.1 CORRECTION (C10, C21, C27) — re-open.** The suite is not implementation-agnostic: fixtures import ai_mesh_gateway.main directly, so with BASE_URL pointed at a closed port 27 tests — including all 6 "live-uvicorn" tests — still pass, and only 3 of 89 cases genuinely test TCP. CI accepts pytest exit 1. Exit additionally requires: every case runs against the configured base URL, proven by a negative-control run against a closed port in which every case fails; CI fails on any failure; the two cases failing at HEAD ("DID NOT RAISE" on blocked requests) are resolved or ledgered; added cases for every SDK text field (C21), provider error classes with headers (400 → BadRequestError, 429 → RateLimitError with Retry-After, context_length_exceeded) and long silent generations (C27). The prototype's ported 18 assertions and its negative control are a working template.


| **Field** | **Value** |
|----|----|
| Phase | Foundation |
| Depends on | GW00 |
| Primary owner | Protocol engineer |
| Objective | Turn the one frozen contract into an executable specification that both v1 and v2 must satisfy, and that runs on every commit. |

### Why this task exists

The single contract that may not change (§10.2.1) is already written: 1,979 lines and 77 tests against openai==2.38.0. It currently imports the v1 application directly and **never runs in CI**, because the only workflow does not install openai. A frozen contract that is not executed on every change is not frozen.

### Implementation work

Move the six conformance files into gateway_v2/contracts/openai_conformance/. Replace direct imports of the v1 app with a fixture that resolves the application under test from an environment variable, so the identical suite runs against v1, v2-in-process, and v2 over real TCP.

Keep test_openai_sdk_compat_live_uvicorn.py's real-socket variant and make it mandatory, not optional: SSE framing over chunked transfer, typed event ordering, content-type on a streamed block, and mid-stream client disconnect are all invisible to ASGITransport.

Add the Node SDK. Install the pinned openai npm package and run the equivalent streaming, tool-call and error-class assertions, because §6's release gate names a Python **and** Node matrix and only Python exists today.

Resolve the two xfail(strict=False) markers (D4 models.retrieve, D5 typed responses stream) — either they pass and become strict, or they are real gaps and enter the ledger as known v1 deviations.

Extend coverage to the surfaces the suite does not reach: MCP JSON-RPC framing, /v1/rag/\* and /v1/vector/\* request and error shapes.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW01-1 | Run the suite against v1 in CI. | Result recorded as the baseline, including any failures, which enter the ledger. | Suite cannot run against v1. |
| LGW01-2 | Run the real-socket variant against v1 behind the staging nginx. | SSE framing, event order and disconnect behaviour recorded at the wire. | Suite only runs in-process. |
| LGW01-3 | Node SDK matrix against staging. | Streaming iterator completes; tool calls reconstruct; typed errors raise. | Any test needs a custom parser or a patched client. |
| LGW01-4 | Break the wire deliberately: emit one malformed data: frame. | Suite fails and names the frame. | Suite passes — it is not testing the wire. |
| LGW01-5 | Run the suite twice against the same build. | Identical results; no flakes. | Non-deterministic result. |

### Failure scenarios that must be handled

v1 fails a conformance test → it is a recorded v1 deviation in the ledger, not a licence to weaken the test. Node and Python disagree → the stricter behaviour is the contract. A test only passes in-process → it is not a wire test; fix or delete it.

### Exit criteria

Suite runs against an app resolved by configuration. Python and Node both in CI on every PR. Real-socket variant mandatory. xfail markers resolved. v1 baseline recorded with every deviation in the ledger.

### Evidence package

CI runs for v1 in-process, v1 over TCP, Node matrix · pinned SDK versions and lockfiles · recorded v1 baseline · ledger entries for deviations · negative-fixture run.

### Rollback / stop rule

None — additive. **Stop** if a conformance assertion cannot be satisfied without an SDK-side workaround: that is a contract violation and the design changes, not the test.

### Agent execution notes

Fully agent-executable. The agent must not modify an assertion to make it pass; a failing assertion is a finding and goes to the ledger.

## GW02 — Build the parity harness: recorded corpus, replay differ, expected-diff ledger

> **v2.1 CORRECTION (C8, C31) — re-open.** C2 must be the declared synthetic distribution of §10.7.1 replayed through the REAL v1 and v2 processes and a streaming recorder; the "v1 oracle" must be v1's live behaviour, not a regex over ATTACK_PATTERNS (live v1 allows the backtick prompts the oracle blocks); the ledger uses the ancestry rule; canary checks must catch split, escaped and partially redacted values (exact-match checks missed the prototype's leaks). LGW02-5 must be rewritten: the "known backtick result" does not exist at the baseline.


| **Field** | **Value** |
|----|----|
| Phase | Foundation |
| Depends on | GW00, GW01 |
| Primary owner | QA/perf engineer |
| Objective | Make "v2 behaves correctly" a measured claim rather than an opinion, and make the deliberate behaviour changes visible and pre-registered. |

### Why this task exists

Greenfield-parallel with one cutover is only safe if divergence is detectable in advance. Because §10.2.2 changes behaviour on purpose, a plain pass/fail differ would report every improvement as a regression. The ledger is what separates the two.

### Implementation work

Build the C2 recorder: capture request shape, headers relevant to routing, plan version, disposition, transformations, provider payload and client bytes across all four surfaces, both streaming modes, streaming and non-streaming, and both tenants of the opposite-policy pair. Sanitize at capture, never after. Target ≥50,000 requests covering every surface and both modes.

Reuse staging/t02/ from the revamp branch — a deterministic token-emitting upstream recorder with nginx, provisioning and teardown scripts already exists and is exactly the controlled provider this needs. Cherry-pick it rather than rebuilding it.

Build the replayer: feed C2 through v1 and v2 against the same recorder, with the same plan versions and the same clock source, and classify each result into IDENTICAL / EXPECTED / UNEXPECTED / WIRE per §10.7.2.

Build the ledger as a version-controlled file. Each entry carries a rule id, the §10.2.2 row it implements, the C3 score that justifies it, the diff signature it authorizes, and its commit timestamp. The differ rejects any entry whose timestamp post-dates the run it would excuse.

Wire C3 scoring: recall and FPR per posture per family, with the paraphrase and developer-traffic families reported separately, because those two are where v1 is known to fail in opposite directions.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW02-1 | Replay C2 through v1 twice. | 100% IDENTICAL against itself. | v1 is non-deterministic on replay — investigate before comparing anything to it. |
| LGW02-2 | Inject a deliberate disposition change into a v2 stub with no ledger entry. | UNEXPECTED; run fails. | Differ misses it. |
| LGW02-3 | Add the matching ledger entry with a back-dated commit; re-run. | Rejected on timestamp. | Post-hoc rationalization accepted. |
| LGW02-4 | Change a response field the SDK parses. | WIRE bucket; run fails; C1 gap logged. | Classified as EXPECTED. |
| LGW02-5 | Score v1 against C3. | Recall/FPR table per posture and family published, reproducing the known backtick and paraphrase results. | Corpus cannot reproduce known v1 behaviour — the corpus is wrong. |

### Failure scenarios that must be handled

C2 under-covers a surface → coverage is reported per surface and a gap blocks GW21, not GW02. Replay is non-deterministic because of time or ordering → inject the clock and fix the ordering before trusting any diff. Sanitization removes something the differ needs → change what is captured, never un-sanitize.

### Exit criteria

C2 ≥50,000 requests with per-surface coverage published. Replayer classifies into four buckets. Ledger enforces pre-registration. C3 scoring reproduces v1's known behaviour. v1 replays identically to itself.

### Evidence package

C2 manifest with coverage by surface and mode · v1 self-replay report · four negative-fixture runs · v1 C3 scorecard · ledger schema and CI gate.

### Rollback / stop rule

None — harness only. **Stop** if v1 cannot replay identically to itself: comparing v2 to a non-deterministic oracle produces meaningless diffs, and the non-determinism must be located first.

### Agent execution notes

Fully agent-executable. The agent may not add ledger entries; those require the human who authorizes the behaviour change, and the timestamp gate enforces it.

## GW03 — Implement the ResourceContract and prohibit capacity literals

> **v2.1 CORRECTION (C10) — re-open.** Measure per-worker RSS and service rate at warm-up instead of defaulting them; make queue_depth a function of the measured service rate and the p99 target; bound the connection pools by real budgets, not the whole fd limit; model guard owner processes and bound guard admission at the owner (the per-worker ≈ 1-window share is what capped the prototype's knee); read limits from parent cgroups; make reload safe — the implemented SIGHUP handler deadlocks 3/3 on a non-reentrant lock and recurses to a crash under rapid signals (register the handler on the event loop; never take locks in a signal handler); decide the 1-vCPU behaviour consistently with T18/GW20; make LGW03-5 count real dropped requests under load; log every detection override as a deviation; adopt the stronger capacity gate.


| **Field** | **Value** |
|----|----|
| Phase | Foundation |
| Depends on | GW00 |
| Primary owner | Platform engineer |
| Objective | Make every bound in the process a function of the environment, so that scaling is a deployment action and never a source edit. |

### Why this task exists

This is the objective's central requirement expressed in code. Today a correct cgroup detector exists and is bypassed in production by WEB_CONCURRENCY: 16, its own output is clamped at 32/16/8/256, and seven further pools are hardcoded outside it entirely.

### Implementation work

Port the detection half of shared/ai_mesh_shared/resource_budget.py verbatim — the cgroup v2 → v1 → sched_getaffinity → RLIMIT_NOFILE chain with minimum-of-signals is correct, tested, and worth keeping. Keep its injectable-root test seams.

Delete every clamp. Replace workers, queue_depth, pool_size and stream_buffer_bytes with functions of CPU quota, memory limit, fd limit, measured service rate and the deployment-declared target_p99_ms and utilization_cap.

Implement the CI gate: an AST pass that flags integer literals appearing as pool sizes, worker counts, queue depths, semaphore limits, buffer sizes and connection limits anywhere outside runtime/resources.py.

Make the contract reloadable on SIGHUP so pools resize without a restart, and log the detected values and any explicit override at startup as a named deviation.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW03-1 | Run the same image at 2, 4, 8 and 16 CPU quota. | Worker count, pool sizes and queue depths differ at each; all logged. | Any value constant across all four. |
| LGW03-2 | Halve the memory limit at fixed CPU. | Buffer and queue ceilings fall; worker count responds per contract. | Memory has no effect. |
| LGW03-3 | Lower RLIMIT_NOFILE below the derived connection budget. | Connection bounds fall accordingly; startup logs the binding signal. | Process exceeds the fd limit under load. |
| LGW03-4 | Add max_connections=64 to a module outside runtime/. | CI gate fails naming file and line. | Gate passes. |
| LGW03-5 | Change CPU quota and SIGHUP. | Pools resize; no restart; no dropped in-flight request. | Requires restart, or drops requests. |
| LGW03-6 | Set an explicit WEB_CONCURRENCY override. | Honoured, and logged as a deviation with the detected value beside it. | Silently honoured. |

### Failure scenarios that must be handled

cgroup files unreadable → documented fallback order, logged, never a silent default. Detected capacity below the minimum to serve → refuse to start with a clear message rather than start degraded. Guard capacity unknown → guard-derived bounds are explicitly unset, not guessed.

### Exit criteria

Every bound derives from the contract. Literal gate live and demonstrated. Reload without restart. Four CPU points produce four configurations. Override logged as deviation.

### Evidence package

Four-point capacity table · memory and fd sensitivity runs · negative-fixture CI run · SIGHUP reload under load · startup log showing detected values and binding signal.

### Rollback / stop rule

Revert to the previous contract revision; bounds are versioned with the image. **Stop** if a bound cannot be derived and a literal is proposed as a workaround — that literal is the defect this task exists to remove.

### Agent execution notes

Fully agent-executable. The agent must not introduce a literal "temporarily"; the gate will reject it and the task is not complete until the derivation exists.

## GW04 — Define the core domain types and prove request state is immutable

> **v2.1 CORRECTION (C1, C21, C22).** The shared types live in the lowest layer, not in detect/base.py, plan/model.py and resolve/decision.py. The request and response types enumerate every text-bearing SDK field (C21) so detectors and egress cannot silently skip a channel. Decision carries per-finding dispositions (C22). LGW04-2 and LGW04-4 move to GW15 (C2).


| **Field** | **Value** |
|----|----|
| Phase | Core |
| Depends on | GW00 |
| Primary owner | Backend architect |
| Objective | Establish one vocabulary — Finding, Decision, ExecutionPlan, RequestContext — and make order-dependent mutation structurally impossible. |

### Why this task exists

v1's proxy_chat mutates body in 28 places and writes a shared stage_metrics dict in 20, across 5,027 lines and 339 branches. effective_prompt is reassigned at five separate points, and scan_text is derived from it \*after\* the policy stage — so whether the scanner sees raw or redacted text depends on whether an earlier conditional ran. check_resp defaults to {} and \_input_decision to None, making "the stage was skipped" indistinguishable from "the stage found nothing". Every one of those is an order-dependent bug waiting for a new branch.

### Implementation work

Define the §10.5 types as frozen slotted dataclasses. Every stage takes a context and returns a new one; nothing mutates in place.

Define the Category enum as the single taxonomy. Every detector, rule and audit field draws its category from it. This is what makes the injection versus prompt_injection divergence in §10.1.3 a compile error.

Make absence explicit. There is no None-means-maybe: a stage that did not run yields a Finding with status SKIPPED, and a stage that could not run yields UNAVAILABLE. No default dictionary, no sentinel empty value.

Add the CI gate that rejects a non-frozen dataclass or a mutable default anywhere in plan/, detect/, resolve/.

Write the property suite: for any stage sequence, the context after stage \*n\* is derivable from the context after stage \*n−1\* and the stage's declared inputs alone.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW04-1 | Attempt to mutate a RequestContext field at runtime. | FrozenInstanceError. | Mutation succeeds. |
| LGW04-2 | Run the full stage sequence in a deliberately shuffled order where dependencies permit. | Identical outcome, or an explicit dependency error; never a silently different verdict. | Outcome depends on order. |
| LGW04-3 | Construct a Finding with a category not in the enum. | Rejected at construction. | Accepted as a string. |
| LGW04-4 | Skip the semantic stage entirely. | SKIPPED findings present and distinguishable from a clean EXECUTED result in the audit record. | The two are indistinguishable. |
| LGW04-5 | Property run, 10,000 generated stage sequences. | No sequence produces a verdict not derivable from declared inputs. | Any hidden dependency found. |

### Failure scenarios that must be handled

A stage genuinely needs to modify content → it returns a Transformation, which dispatch/ applies and verifies; it never edits the body itself. Two detectors report the same category → both findings are retained and the resolver decides; neither overwrites the other.

### Exit criteria

All core types frozen. One category enum. SKIPPED and UNAVAILABLE are first-class and visible in audit. Immutability gate live. Property suite green.

### Evidence package

Type definitions · frozen and enum gate runs · order-shuffle report · property-suite output · sample audit record showing all three statuses.

### Rollback / stop rule

Revert the type module; nothing depends on it yet. **Stop** if a stage cannot express its work as a returned value — that stage's design is wrong and it is redesigned, not exempted.

### Agent execution notes

Fully agent-executable. The agent must not add unsafe_hash, eq=False or a mutable field to work around immutability; those are gate violations.

## GW05 — Build the plan compiler, distribution and snapshot with three distinct plan states

> **v2.1 CORRECTION (C3, C22, C28, C36).** Data loss is not the same as unavailability (C36): plans, keys, kill switches and the auth epoch need a durable source of truth with re-hydration; versions are (epoch, sequence) + content hash and strictly monotonic (the prototype accepted regress, and a reused version string made the output phase enforce stale content and deliver raw PII); missing plan data is PLAN_UNAVAILABLE (503), never unknown tenant (a flush + re-seed wedged tenants at 403 forever); last-known-good is durable. The control-plane writer side (field-by-field mapping of the 65 FirewallConfig fields, save-time rejection shown in the console, applied-version API) is owned jointly with GW14b. The compiler rejects plans the resolver cannot represent (C22) and unsupported selections such as STRICT_WITHHOLD before it exists (the prototype silently served it as INCREMENTAL). Distribution must be correct under concurrency and scale: in the prototype, concurrent reconciles reverted plans while the version index said current, leaving 75–83 of 2,000 tenants stale and 413 never loaded on a live 4-worker server — while plan_snapshot_age stayed < 1 s and /readyz was ready — and reconcile was O(orgs) on every worker's event loop (100k orgs: 55 ms stall per second). Use one serialized reconcile task per process fed by pushes, compare against the served plan's own version, reconcile deltas off the serving loop, prove convergence (LGW05-4) by sampling the version actually served, and keep metric cardinality independent of tenant count (50k tenants gave 50,003 series per worker). A reconcile period equal to the staleness limit caused spurious fail-closed 503s: the freshness bound must exceed the reconcile period. LGW05-3 moves to GW20; LGW05-7's console part to GW14b/UI07.


| **Field** | **Value** |
|----|----|
| Phase | Core |
| Depends on | GW04 |
| Primary owner | Backend + control-plane engineer |
| Objective | Move all configuration interpretation off the hot path into one validated, versioned artifact, and make "unavailable" impossible to confuse with "nothing selected". |

### Why this task exists

v1 resolves Tier-2 enablement in four separate implementations across six call paths, with opposite defaults on input and output for the same unset value. Per-org config is built as {\*\*platform_config, \*\*org_data}, so every key a tenant did not set silently takes the platform value. PolicySync.is_loaded is a single global flag, so org B is reported ready on org A's bundle and then evaluates against an empty rule set. These are not separate bugs; they are one missing artifact.

### Implementation work

Build the compiler in the control plane. It takes the organization's selections, validates them against the supported detector catalogue and the declared threshold ranges, rejects unsupported combinations with an actionable error, derives required_detectors, and emits a versioned immutable ExecutionPlan. An unsupported selection fails **at save time, in the console**, not silently at request time.

Build distribution: push on change, plus a periodic reconcile so a missed notification has a bounded lifetime rather than an unbounded one. Every replica exports plan_snapshot_age_seconds per org.

Implement the three states of §10.5.2 as distinct types, not as an empty dictionary. PLAN_UNAVAILABLE and PLAN_UNKNOWN_TENANT cannot be constructed from a missing key; they are returned explicitly.

Make every surface — chat, MCP, RAG, vector, embeddings — consume the same pinned plan. There is exactly one resolve\_\*\_enabled function in the codebase, and it is the plan lookup.

Pin the plan version once per request and carry it in the context. Input and output resolve against the same version even if a push lands mid-request. Record the version in every audit record.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW05-1 | Tenant authorized, zero optional rules selected. | Valid plan, rules=(); platform integrity still applies; no content detector runs. | Treated as unavailable, or platform defaults appear. |
| LGW05-2 | Make the plan store unreachable for a known tenant. | PLAN_UNAVAILABLE; signed not-ready behaviour; **never** another tenant's or the platform posture. | Any inherited configuration. |
| LGW05-3 | Set one rule to ENFORCE; leave every tri-state unset. | Identical resolution on input and output and on all four surfaces. | Any surface disagrees. |
| LGW05-4 | Change a rule action at 70% load. | All ready replicas converge within the declared freshness bound; plan_snapshot_age_seconds proves it. | Any replica serves the old plan past the bound. |
| LGW05-5 | Push a plan mid-request. | That request completes entirely on its pinned version; input and output agree. | Input and output use different versions. |
| LGW05-6 | Two tenants with opposite plans under concurrency. | No cross-tenant leakage of config, findings or version. | Any leakage. |
| LGW05-7 | Save an unsupported detector/action combination in the console. | Rejected at save with an actionable message. | Accepted and silently ignored at request time. |

### Failure scenarios that must be handled

Compiler rejects a plan a tenant already had → the previous valid version keeps serving and the tenant is told; never fail to an empty plan. Store flaps → last-known-good serves within the freshness bound, then not-ready; never silent staleness. Two replicas hold different versions → both are valid within the bound and both are recorded.

### Exit criteria

One compiler, one plan type, one lookup. Three states distinct and tested. All surfaces on the pinned plan. Convergence bound measured under load. Version in every audit record. Console rejects invalid selections at save.

### Evidence package

Compiler validation matrix · three-state tests · cross-surface parity report · convergence measurement · mid-request pin proof · two-tenant isolation run.

### Rollback / stop rule

Roll back the plan schema version; replicas serve the last valid compiled version. **Stop** if any surface needs its own enablement logic — that is the defect returning and the surface is brought onto the plan instead.

### Agent execution notes

Agent-executable. The agent must not add a per-surface default "for compatibility"; §10.2.2 authorizes the semantic change and the ledger records it.

## GW06 — Build the admission layer with bounded shared state and one round trip

> **v2.1 CORRECTION (C12, C24, C26, C29, C36).** A kill switch stored as "key present = on" fails OPEN on data loss: after a failover with replication lag the prototype silently disengaged an engaged switch and accepted an epoch regress that un-revoked a key — store the switch with an explicit OFF record, treat absence or regress as UNAVAILABLE (fail closed), fill caches only if the epoch is unchanged since the fetch began, keep store-op timeouts below the refresh period, and retry idempotent reads once after connection errors (dead pooled connections produced 75/150 spurious 503s). The posture table is signed by a human and is the single place admission outcomes are decided. Quota leases must use a TTL on the store's clock and return unspent budget (prototype: 39 of 120 requests got 429 with budget remaining; a crashed worker's lease is lost); per-org rate limits must not multiply by worker or replica count. Invalid keys must be negatively cached (5,000 random keys caused 5,000 shared-store reads). Cheap size and structure pre-checks run before expensive parsing. In-flight semantics for kill switch, key revocation and plan change are signed and tested on a 5-minute stream (the prototype delivered ~93% of a stream after each). Prototype evidence: warm steady state ≥ 27 of 30 requests made zero shared-state round trips; an org kill-switch reached every worker within 0.25–0.44 s.


| **Field** | **Value** |
|----|----|
| Phase | Core |
| Depends on | GW03, GW04 |
| Primary owner | Backend engineer |
| Objective | Resolve identity, quota and kill-switch truthfully within a single shared-state round trip, with one coherent failure posture. |

### Why this task exists

v1 performs an authentication round trip per request, a kill-switch read with no cache, an unmetered model_state read that is not a named stage, and a serial burst/RPM multi. Worse, one Redis fault produces opposite postures simultaneously: rate_limiter.py:116,169,230 return allow, while circuit_breaker.py:397-400 returns block on any exception. A single outage removes rate limiting and blocks all traffic at the same time.

### Implementation work

Identity: RAM cache keyed by key hash, invalidated by a monotonic auth_epoch so revocation is bounded and does not require a per-request read. Shared store is consulted on miss only.

Quota: local GCRA for burst and rate; a shared **lease** for org-level token budget so N replicas cannot admit N× the limit. Lease chunk size derives from the ResourceContract, not a constant.

Kill-switch: age-bounded RAM snapshot with an explicit staleness ceiling. Past the ceiling, it is UNAVAILABLE and the signed posture applies. Kill-switch remains fail-closed and that is a platform-integrity property a tenant cannot disable.

Declare one failure posture table for shared state, covering identity, quota, kill-switch and plan, and implement it in one place so two components cannot disagree about the same outage.

Every shared-state operation is bounded: timeout, pool size and retry budget all derive from the contract, and every one is timed into the trace.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW06-1 | Steady load, warm caches. | ≤1 shared-state round trip per request, proven by store-side operation counters. | More than one in the steady state. |
| LGW06-2 | Revoke a key. | Rejected within the declared epoch bound on **every** replica. | Any replica still accepts past the bound. |
| LGW06-3 | Four replicas against one org quota. | Aggregate admission ≤ limit + declared lease overshoot; overshoot published. | Quota multiplies by replica count. |
| LGW06-4 | Kill the shared store entirely. | Exactly the declared posture; identical across quota, kill-switch and plan; no component disagrees. | Two components take opposite actions. |
| LGW06-5 | Inject 200 ms store latency. | Bounded timeout; declared posture; no unbounded pool growth. | Latency propagates to request latency without a bound. |
| LGW06-6 | Fail over the store. | Recovery within the declared window; posture holds throughout. | Undefined behaviour during failover. |

### Failure scenarios that must be handled

Store slow but alive → timeout is the boundary, not liveness. Epoch bump missed → periodic reconcile bounds staleness. Lease held by a crashed replica → lease TTL reclaims; TTL derives from the contract.

### Exit criteria

One round trip warm. Bounded revocation, proven multi-replica. No quota multiplication. One posture table, implemented once, verified under total outage. All operations timed and bounded.

### Evidence package

Store operation counters · revocation timing per replica · four-replica quota report with overshoot · outage matrix · latency-injection run · failover run.

### Rollback / stop rule

Revert to the previous admission revision. **Stop** if any fault produces two different postures in two components — that is the v1 defect and it is fixed before proceeding.

### Agent execution notes

Agent-executable. The posture table is signed by a human before implementation; the agent implements it, it does not choose it.

## GW07 — Build the single pure resolver and prove nothing else can decide

> **v2.1 CORRECTION (C11, C22).** Implement per-finding resolution (C22) with its two added properties; add the sealed constructor, provenance gate and runtime assertion (C11); LGW07-7 must also prove that a detector-side raise and a detector importing HTTP types fail CI. LGW07-1 and LGW07-4 move to GW15.


| **Field** | **Value** |
|----|----|
| Phase | Core |
| Depends on | GW04, GW05 |
| Primary owner | Security engineer |
| Objective | Concentrate every security outcome in one pure function and make any second decision site a build failure. |

### Why this task exists

This is the task that removes the defect class of §10.1.1. Today 283 terminal-decision sites exist across 25 modules; org intent is a severity maximum rather than a precedence (P1, P2); a degraded output scan makes the action independent of the verdict (P3, P6); MONITOR does not neutralize enforcement on output (P7); and an adapter can erase a finding before the resolver sees it.

### Implementation work

Implement resolve() exactly as §10.5.3 specifies, in strict step order, with no severity maximum anywhere. Conflicts resolve by explicit priority with a documented tie-break.

Route UNAVAILABLE through the owning rule's on_unavailable posture. There is no generic degraded branch. This is the direct removal of P3 and P6: there is no code path in which the emitted action is computed from anything other than the findings and the plan.

Make MONITOR structurally non-enforcing: monitor findings are recorded and are not candidates. This removes P7.

Make the resolver pure and enforce it: no imports of I/O modules, no clock, no globals. A CI gate checks the import set of resolve/.

Mint DispatchAuthorization only for non-blocking dispositions, and make it the required argument of the provider client's entry point. BLOCK then cannot reach a provider, by type.

Add the property suite: no disposition arises that is not derivable from an ENFORCE rule; adding a MONITOR rule never changes a disposition; adding a SKIPPED finding never changes a disposition; the function is deterministic across 10,000 generated inputs.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW07-1 | Enabled semantic injection returns BLOCK, no regex rule matches. | Zero provider calls; audit names the deciding rule and model version. | Finding discarded, request allowed — the v1 adapter defect. |
| LGW07-2 | Same finding, organization action FLAG. | Provider called; finding present; transport allow + flagged. | Silently becomes BLOCK. |
| LGW07-3 | Org action ALLOW, detector recommends BLOCK. | Resolves per signed rule semantics, not a severity max. Result matches the ledger entry. | Reproduces P1. |
| LGW07-4 | Output semantic detector UNAVAILABLE, rule posture FAIL_CLOSED. | BLOCK. Control with FAIL_OPEN → the rule's declared action. | Any generic degraded path emits REDACT regardless of verdict — P3/P6. |
| LGW07-5 | Rule in MONITOR with a high-confidence finding. | Disposition unchanged; finding recorded. | MONITOR enforces — P7. |
| LGW07-6 | PII=REDACT and Credentials=BLOCK on one prompt. | BLOCK by priority; both findings retained; no provider call. | Redaction cancels the block, or one finding is lost. |
| LGW07-7 | Add a status_code=403 in dispatch/. | CI AST gate fails. | Gate passes. |
| LGW07-8 | Property suite, 10,000 generated cases. | All four properties hold. | Any violation. |

### Failure scenarios that must be handled

Two ENFORCE rules with equal priority → documented deterministic tie-break, recorded in the decision. A detector returns an unmapped category → construction fails at GW04; it cannot reach the resolver. A transformation cannot be applied → dispatch/ reports failure and the request takes the rule's unmaskable path; it is never dispatched unverified.

### Exit criteria

One resolver, pure, gated. No severity max. UNAVAILABLE routed per rule. MONITOR structurally non-enforcing. BLOCK unreachable by a provider, by type. All eight tests and the property suite green.

### Evidence package

Full action matrix across disposition × mode × availability · provider call counts per case · purity and AST gate runs · property output · audit records naming deciding rules.

### Rollback / stop rule

Revert the resolver revision. **Never** route around the resolver to restore availability — that reconstructs the defect this task exists to remove.

### Agent execution notes

Agent-executable, and the highest-scrutiny card in the track: human review is mandatory before merge. The agent must not add a convenience branch that returns an action outside the six documented steps.

## GW08 — Build the detector framework and the GuardBackend interface

> **v2.1 CORRECTION (C7, C23, C30, C35).** Backends: `local_gpu` = one owner process per GPU (not per-worker in-process sessions); `triton_grpc` only with exact-shape plans (dynamic-profile plans measured 1.8 ms/window slower and gave zero capacity at p99 ≤ 10 ms); `local_onnx` (CPU) is development/CI only — best 74 ms per window, and int8 flips 2–6% of decisions against fp32. Owner admission is per tenant; readiness requires recent successful replies; a dead or wedged owner makes pending and new calls UNAVAILABLE within a bounded time (prototype: 12.9 s recovery after SIGKILL, zero fabricated clean results; an owner with a hung inference thread kept readiness green — fixed only by reply-based readiness). Model identity must be computed from the bytes actually loaded (the prototype advertised the pinned hash while running a different model, and detection changed silently) and compared with a signed manifest. Engine caches keyed by model, TensorRT and driver version; staged engine rollout. LGW08-1 moves to GW15; LGW08-6 to GW19.


| **Field** | **Value** |
|----|----|
| Phase | Detection |
| Depends on | GW04, GW07 |
| Primary owner | Backend engineer |
| Objective | Make detectors uniform, replaceable and incapable of deciding anything, and make the semantic backend a deployment choice. |

### Why this task exists

v1's detectors variously mutate the body, return HTTP responses, short-circuit the pipeline and disagree about the same concept's name. The semantic backend is bound into scanner semantics, so changing accelerator topology means editing security code — the opposite of the scalable-code doctrine in §2.1.

### Implementation work

Define the Detector protocol: takes text and plan context, returns Finding\[\], declares its required_capabilities and detector_version. It cannot import edge/, cannot raise HTTP errors, cannot mutate.

Define GuardBackend per §10.5.4 with four implementations behind it: local_onnx (CPU, the default for development and CI), local_trt (in-process TensorRT), triton_grpc (node-local), remote_http (off-box burst). Selection is deployment configuration and the detector cannot observe which is active.

Build the registry: required_detectors from the plan resolves to instances at snapshot load, not per request.

Implement readiness. A backend that is not ready makes its detectors UNAVAILABLE; it never makes them clean. Readiness includes model_hash, and the hash appears in every finding and every audit record.

Implement capacity_hint() so runtime/resources.py can derive guard-dependent bounds from measured service rate rather than a constant.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW08-1 | Run the identical detection suite on all four backends. | Same findings within the declared tolerance; no semantic difference attributable to topology. | Any backend changes the security outcome. |
| LGW08-2 | Swap backend by configuration only, no code change. | Works; model_hash changes; audit reflects it. | Requires a source edit. |
| LGW08-3 | Kill the backend mid-load. | Selected detectors go UNAVAILABLE; rule posture applies; zero fabricated clean results. | Any benign result from an unavailable detector. |
| LGW08-4 | Corrupt the engine cache. | Not-ready; refuses to serve those detectors; no silent CPU fallback presented as the selected model. | Silent substitution. |
| LGW08-5 | Add an HTTP raise inside a detector. | CI gate fails. | Gate passes. |
| LGW08-6 | Backend degrades to half throughput. | capacity_hint changes; admission bounds respond; no unbounded queue. | Bounds unchanged; queue grows. |

### Failure scenarios that must be handled

Backend is slow rather than down → budget timeout yields UNAVAILABLE, which is a different finding from a clean pass. Model hash changes under a running process → treated as a new version; caches keyed by hash are invalidated.

### Exit criteria

Detectors emit findings only. Four backends behind one interface, swappable by config. UNAVAILABLE never becomes clean. model_hash in findings and audit. capacity_hint feeds the contract.

### Evidence package

Four-backend equivalence report · config-only swap · kill and corruption runs · AST gate run · degradation and bounds response.

### Rollback / stop rule

Revert to the previously validated backend by configuration; no code change required, which is the point of the interface. **Stop** if a backend cannot express unavailability distinctly from a clean result.

### Agent execution notes

Agent-executable. local_onnx is the default for CI so the track never depends on GPU availability to make progress.

## GW09 — Implement deterministic detectors with byte-verified redaction

> **v2.1 CORRECTION (C21, C26).** Scan every text-bearing request field, the joined parts of each message and decoded tool-argument values, with span maps (C21). Carry T07's L07-2 into this card and declare the decoder set: validation passed single %-encoding, fullwidth, zero-width and \u escapes but missed double %-encoding, HTML entities, base64, Cyrillic homoglyphs, RTL-reversed keys and "at/dot" e-mails; the prototype's control-character list covered 22 of 2,155 Default_Ignorable/Mn code points. Bound canonicalization cost before rejection (an 18× NFKC-expanding 1 MiB body cost 7.4 s CPU on the event loop). The v1 redactor divergence comes from different text units plus a system-message skip, not a weaker redactor. One Hyperscan pass costs ~30 µs per 5 KB (Python `re`: 1.9 ms); an `auto` fallback to `re` loses Unicode word boundaries. LGW09-2 moves to GW15.


| **Field** | **Value** |
|----|----|
| Phase | Detection |
| Depends on | GW08 |
| Primary owner | Security engineer |
| Objective | Fast, deterministic PII/secret/credential/transport detection whose redaction is verified on the bytes that actually leave, and whose false positives are scored rather than assumed. |

### Why this task exists

Two separate v1 problems meet here. First, the decision is made against one redactor (INPUT_SCANNER.redact_pii on a flattened prompt) while the wire is produced by a different one (llm_router.\_apply_redaction → patterns.redact_all, whose own docstring calls it "by construction WEAKER"). The system can therefore decide REDACT and emit something the decision never inspected. Second, ATTACK_PATTERNS\["command_injection"\] contains \` \[^\]+ \` — any backtick pair — which we confirmed matches 5 of 5 benign inline-code prompts, and command_injection\` is outside the explanatory carve-out, so those blocks are terminal.

### Implementation work

One canonicalization pass with a declared decode budget, then one multi-pattern match. No per-pattern loop over the text.

Exactly one redactor. The bytes the resolver inspected are the bytes dispatch/ sends, and dispatch/ re-verifies the transformed payload against the finding spans before the provider call. A redaction that did not remove what it claimed is a failure, not a pass.

Demote injection and command patterns from terminal block to **signal**. They contribute a finding; the plan decides the action. This is a §10.2.2 behaviour change and requires a ledger entry and a C3 score.

Score every detector against C3 and publish recall and FPR per family, with the developer-traffic family reported separately. This is where the backtick fix is proven rather than asserted.

Keep byte-verified fail-closed semantics for unmaskable content: if a transformation cannot be applied safely, the rule's unmaskable path runs; the payload is never dispatched unverified.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW09-1 | Five benign inline-code prompts from the developer-traffic family. | Zero terminal blocks; findings present as signal only. | Any terminal block — the v1 defect survives. |
| LGW09-2 | PII fixture with REDACT, streaming and non-streaming. | Recorder shows sanitized bytes; raw value absent from provider payload, logs, traces and error bodies. | Raw value anywhere. |
| LGW09-3 | Secret split across a chunk and a UTF-8 boundary. | No leak; protocol stays valid. | Leak or malformed frame. |
| LGW09-4 | Obfuscated/encoded PII fixtures. | Detected within the declared decode budget, or explicitly rejected on budget. | Silent miss. |
| LGW09-5 | Transformation deliberately made a no-op. | Detected pre-dispatch; unmaskable path runs; nothing dispatched. | Dispatched as if redacted. |
| LGW09-6 | C3 scoring run. | Recall/FPR per family published; developer-traffic FPR materially below v1's. | Not measured, or no improvement. |

### Failure scenarios that must be handled

Decode budget exhausted → explicit reject, never silent truncation. Pattern is a superset prefilter → verified by the precise matcher before a finding is emitted. Fix reduces recall on a real family → ledger records the trade and it is signed, not absorbed.

### Exit criteria

One canonicalization, one matcher, one redactor. Wire bytes verified against spans. Injection demoted to signal with ledger entry and C3 score. Per-family recall/FPR published.

### Evidence package

C3 scorecard per family · wire captures for redact cases · boundary tests · no-op detection run · ledger entries · decode-budget report.

### Rollback / stop rule

Revert the detector ruleset version; rulesets are versioned and hashed. **Stop** if the wire bytes cannot be verified against the spans the decision used.

### Agent execution notes

Agent-executable. The agent must not tune a pattern to make a corpus case pass; corpus scores are outcomes, not targets.

## GW10 — Implement semantic detection, windowing and the per-request FPR budget

> **v2.1 CORRECTION (C6, C7).** "Batch windows into one backend call" is wrong on L4: per-window cost rises with batch size (2.15 → 3.28 ms/window) and every micro-batching setting measured was worse than batch 1. Disable the shipped tokenizer.json's truncation and padding at 512. Calibrate thresholds per window count on long benign text (short-prompt thresholds fail on long windows), publish recall and request-level FPR per W, per language and per family at the chosen thresholds — measured today: in-scope recall 35–40%, paraphrase 19–32%, native-script Indic 0/12 (22M) vs 8/12 (86M), with benign developer SQL at 0.99 and benign confidentiality system prompts at 0.998. Add language identification with an explicit posture for unsupported languages (an unsupported language must not look like a clean EXECUTED pass), confusable mapping before the guard tokenizer, and role-scoped semantic rules (system prompts and assistant history are not user input). The independence formula for request FPR was confirmed near 1% per window. PG2-86M is excluded from the 20 ms profile; the 22M-vs-86M trade (Indic recall vs latency) must be signed in T01.


| **Field** | **Value** |
|----|----|
| Phase | Detection |
| Depends on | GW08 |
| Primary owner | ML/security engineer |
| Objective | Add semantic injection detection with a published, per-request false-positive rate and no silent truncation of long inputs. |

### Why this task exists

The model's published operating point is per window, not per request. A model quoted at 1% FPR at 512 tokens compounds across windows: at two windows a request sees 1 − 0.99² ≈ **1.99%**, and at seven windows ≈ **6.79%**. In an ENFORCE posture at 1,000 RPS that is roughly 20 false blocks per second at two windows. This number has never been published alongside the latency figure, and §10.2.2 requires it before any customer-facing claim.

### Implementation work

Tokenizer-aware windowing with declared overlap. Window count is a function of measured characters-per-token for the actual traffic, not the 4.0 rule of thumb — measured values in this corpus are 3.04 for markdown and 6.28 for prose, so a 1,024-token band is about 3,158 characters of markdown, not 4,096.

Batch windows into one backend call; never loop serially. Aggregate by the declared rule, and record the window count on every request.

Implement the FPR budget: the plan declares a per-request FPR ceiling; the resolver's confidence threshold is derived from it and the actual window count. Exceeding the ceiling is a recorded condition with a declared behaviour, not a surprise.

Handle over-length inputs explicitly: either every window is covered, or the request is rejected on length with a clear error. Head-and-tail truncation that silently drops the middle is prohibited — that is v1's \_head_tail behaviour at bedrock_scanner.py:63-72 and it is a silent hole.

Publish multilingual behaviour separately. Published multilingual AUC is materially below English, and this is a Mumbai-region deployment.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW10-1 | Attack in the first, middle and last window of a long input. | All three detected, or explicit length rejection. | Middle-window miss. |
| LGW10-2 | Measure FPR at 1, 2, 4 and 7 windows on the benign corpus. | Measured curve published and within the declared ceiling. | Not measured, or ceiling exceeded silently. |
| LGW10-3 | Batch versus serial at equal window count. | Batched; p99 within budget; serial path absent. | Serial loop on the hot path. |
| LGW10-4 | Multilingual corpus. | Per-language recall/FPR published. | English-only claim. |
| LGW10-5 | Input exceeding the maximum window count. | Explicit reject with an actionable error. | Silent truncation. |
| LGW10-6 | Backend timeout mid-batch. | UNAVAILABLE; rule posture applies. | Partial results presented as complete. |

### Failure scenarios that must be handled

Window count varies with tokenizer version → tokenizer hash is pinned and recorded. FPR ceiling forces a threshold that collapses recall → the trade is published and signed, never silently resolved in favour of the latency headline.

### Exit criteria

Windowing measured, not assumed. Per-request FPR published at every deployed window count. Batched inference. No silent truncation. Multilingual published separately.

### Evidence package

Window-count distribution from real traffic · FPR curve · batch-versus-serial latency · multilingual scorecard · over-length rejection · tokenizer and model hashes.

### Rollback / stop rule

Disable semantic rules by plan; deterministic detection continues. **Stop** if the per-request FPR at the deployed window count cannot be published.

### Agent execution notes

Agent-executable against local_onnx. GPU-specific throughput belongs to GW20; this card is about correctness and the FPR budget.

## GW11 — Build provider dispatch with verified transformation and no firewall-initiated generation

> **v2.1 CORRECTION (C11, C27).** Enforce "not constructible outside resolve/" with the sealed constructor and provenance gate. Map provider errors exactly (400 → 400 with the provider's code, 429 → 429 with Retry-After and rate-limit headers; the prototype's blanket 502 made the SDK retry three times). Timeouts come from the plan per provider/model, so a legitimately slow first token (125 s) is not cut at a fixed 120 s. Bound non-stream response size (RSS grew 3× the body; one BYOK tenant could OOM a shared worker). Prototype evidence: aiohttp costs 124–238 µs CPU per JSON request vs 950–2,659 µs for httpx. LGW11-3 (real redactor part) moves to GW15.


| **Field** | **Value** |
|----|----|
| Phase | Dispatch |
| Depends on | GW07 |
| Primary owner | Backend engineer |
| Objective | One place where a provider is called, reachable only with authorization, sending only verified bytes. |

### Why this task exists

v1 has multiple dispatch paths with independently assembled arguments, and the output rewrite helper calls LLM_ROUTER.acompletion on the guard's behalf — a model call the customer did not request, made by the firewall. Separately, reload_models_now runs on the request path and mutates the shared router's model list under a per-org lock, which the function's own docstring records as having previously caused 422 no_provider_configured under concurrency.

### Implementation work

One ProviderClient entry point, requiring a DispatchAuthorization that only resolve/ mints. Routing is deterministic from the pinned plan; no adjudicator call, no shared mutable router state on the request path.

Apply Decision.transformations and verify the result against the finding spans before the call. Verification failure is terminal.

Prohibit firewall-initiated generation: dispatch/ is callable only from the request path. REWRITE, where supported, runs through GuardBackend against a local model.

Catalogue resolution is a snapshot, like the plan. An empty catalogue is an explicit provisioning error distinguishable from a control-plane outage, so a store blip never surfaces to a tenant as "connect your provider".

Bound the connection pool from the ResourceContract and export pool wait time.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW11-1 | Attempt dispatch without an authorization value. | Type error at build time; no runtime path exists. | Runtime bypass possible. |
| LGW11-2 | BLOCK decision. | Recorder shows zero calls. | Any call. |
| LGW11-3 | REDACT decision. | Recorder shows sanitized bytes; verification logged. | Raw bytes, or unverified dispatch. |
| LGW11-4 | REWRITE with the local model. | Zero provider calls attributable to the firewall. | Firewall-initiated generation. |
| LGW11-5 | Empty catalogue versus store outage. | Two distinct errors; neither says "connect your provider" for an outage. | Conflated. |
| LGW11-6 | 500 concurrent requests across 20 orgs. | Zero spurious provisioning errors; no shared-state corruption. | Any no_provider_configured not caused by actual absence. |

### Failure scenarios that must be handled

Provider returns a malformed body → protocol error, not success. Provider slow → bounded timeout from the contract. Pool exhausted → bounded queueing with backpressure, never an exception storm.

### Exit criteria

Single authorized entry point. Verified transformations. No firewall-initiated generation. Snapshot catalogue. Bounded, instrumented pool.

### Evidence package

Type-level authorization proof · recorder call counts per disposition · verification logs · concurrency run · error-distinction tests · pool metrics.

### Rollback / stop rule

Revert the dispatch revision. **Stop** if any code path can reach a provider without authorization.

### Agent execution notes

Agent-executable. The DispatchAuthorization type must not be constructible outside resolve/; the agent must not add a public constructor for convenience.

## GW12 — Build the SSE egress pipeline with bounded buffers, backpressure and real cancellation

> **v2.1 CORRECTION (C4, C24, C25, C27).** Export T_release_processing and T_holdback_wait separately; make scanning linear in bytes with a bounded held-byte ceiling (C25); add write and idle timeouts so a client that stops reading cannot hold a stream and block drain forever (prototype: still alive 90 s after SIGTERM); a maximum stream duration and signed in-flight semantics for kill switch, revocation and plan change (C24); scan provider mid-stream error frames; tolerate split UTF-16 surrogate escapes the way SDK clients do. Streams are forwarded by loops that run no CPU-bound work longer than a declared slice (C38): add a test that injects a CPU burst on a worker mid-stream and requires the other streams' worst-chunk p99 to stay within budget. LGW12-7 duplicates LGW15-1 and moves to GW15; LGW12-8 moves to GW13 and measures bytes scanned per byte released, not invocation counts.


| **Field** | **Value** |
|----|----|
| Phase | Protocol |
| Depends on | GW03, GW11 |
| Primary owner | Protocol engineer |
| Objective | A streaming implementation whose memory is bounded, whose cancellation is real, and whose frames the official SDKs parse. |

### Why this task exists

v1's rewrite path returns without clearing the buffer on every non-DONE flush, so the buffer accumulates the whole response and each flush re-inspects all of it — memory linear in stream length and O(N²) guard cost. Client disconnect is polled and ends the generator but never aborts the upstream request, so a stalled provider is never noticed and inference continues for a client that has gone. There is no backpressure anywhere.

### Implementation work

One SSE state machine producing frames the official SDKs parse, including the terminal marker and the error frame shape.

One bounded coalescer. High-water mark from the ResourceContract as a function of active stream count and memory limit. Exceeding it applies backpressure; it never grows.

Credit-based flow control: a slow consumer slows upstream reads. Memory is bounded by active streams × ceiling, and that product is a derived, exported number.

Real cancellation: client disconnect cancels the provider request and any in-flight guard work within a bounded interval, and the interval is measured.

Never splice a fallback into a stream that has already emitted content. Before the first byte a signed retry may run; after it, the only truthful outcomes are clean termination or a declared error frame.

Export per-request output-detector invocation count, release lag and buffer high-water.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW12-1 | 1,000 concurrent streams, 4,000-token responses. | Memory plateaus at the derived bound; no growth with stream length. | Linear growth. |
| LGW12-2 | Consumer reads at 1 chunk/second. | Backpressure upstream; bounded buffer; no fleet memory growth. | Unbounded buffering. |
| LGW12-3 | Client disconnects after N chunks. | Provider and guard work stop within the bound; resources return to baseline. | Work continues. |
| LGW12-4 | Provider fails before first content. | Only the signed safe retry runs. | Unsafe retry. |
| LGW12-5 | Provider fails after partial delivery. | Clean termination; no spliced fallback. | Two responses concatenated. |
| LGW12-6 | Malformed upstream SSE. | Protocol error or declared degraded outcome; never success. | Reported as success. |
| LGW12-7 | Full conformance suite over real TCP. | All streaming tests pass, both SDKs. | Any failure. |
| LGW12-8 | Measure output-detector invocations per request. | Matches the plan-derived schedule; recorded. | Per-flush invocation as in v1. |

### Failure scenarios that must be handled

Client vanishes without TCP close → idle timeout from the contract. Provider stalls mid-stream → bounded inter-chunk timeout. Guard slower than the stream → backpressure, not buffering.

### Exit criteria

Bounded memory proven at 1,000 streams. Backpressure demonstrated. Cancellation measured. No post-byte splicing. Conformance green over real TCP. Invocation count matches schedule.

### Evidence package

Memory plateau chart · slow-consumer run · cancellation timing · failure-injection matrix · TCP conformance run · release-lag and high-water distributions.

### Rollback / stop rule

Revert the egress revision. **Stop** if memory cannot be bounded independently of response length.

### Agent execution notes

Agent-executable. Buffer sizes come from ResourceContract; the agent must not introduce a literal high-water mark.

## GW13 — Implement output enforcement and the two explicit streaming modes

> **v2.1 CORRECTION (C4, C21, C25).** Scan every text-bearing output field — content, refusal, reasoning, tool/function arguments (decoded), audio transcript, **logprobs tokens and bytes**, annotations — in SSE and JSON, with n = 1 and n = 2; the prototype's output REDACT was defeated by `logprobs=true` while its audit record said REDACT. Structured outputs must still validate after redaction. STRICT_WITHHOLD must be built and qualified here (the prototype lacked it). Qualify INCREMENTAL with the C4 split and a signed holdback bound. LGW13-5 moves to GW20.


| **Field** | **Value** |
|----|----|
| Phase | Protocol |
| Depends on | GW07, GW12 |
| Primary owner | Security + protocol engineer |
| Objective | Make the output path use the same resolver as the input path, and make the two streaming modes honestly distinct at the wire and in the latency claim. |

### Why this task exists

v1's output path has its own enforcement function with its own downgrade rules (P3, P6, P7), and a streaming "block" is a truncation: secure_streaming.py:551-570 emits an error frame after earlier flushes have already reached the client. An operator who selected BLOCK received partial delivery. §1.2 already states that strict withholding and incremental streaming are different products; this card makes the code agree.

### Implementation work

Output uses resolve() with phase=OUTPUT. There is no enforce_output; the same six steps apply, so a degraded output scan can no longer make the action independent of the verdict.

Implement INCREMENTAL: detectors run on a content-defined schedule; BLOCK is expressible only before the first content byte; after that the truthful outcomes are terminate-with-declared-error or redaction of not-yet-released content.

Implement STRICT_WITHHOLD: no content released until the whole response is inspected. It is selected per plan, and its latency profile is qualified **separately** — it may not inherit the incremental TTFT claim.

Make the mode visible: the response carries which mode produced it, and the console shows the tenant which they selected and what it costs.

Qualify each mode independently: release lag and first-content delay for incremental, completion overhead for strict.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW13-1 | Unsafe whole response, STRICT_WITHHOLD. | Zero prohibited assistant bytes before the terminal result. | Any prohibited byte. |
| LGW13-2 | Unsafe content mid-stream, INCREMENTAL. | Declared termination semantics; no claim that an already-delivered prefix was blocked. | Truncation presented as a block. |
| LGW13-3 | Output BLOCK with the semantic detector UNAVAILABLE. | Rule's on_unavailable posture; never a blanket redact. | Reproduces P3/P6. |
| LGW13-4 | Output rule in MONITOR. | Content delivered; finding recorded. | MONITOR enforces — P7. |
| LGW13-5 | Latency for both modes at the same load. | Two separate profiles published; strict never quoted as incremental. | One number for both. |
| LGW13-6 | Secret spanning a chunk boundary in both modes. | No leak in either. | Leak in either. |

### Failure scenarios that must be handled

Provider emits everything in one chunk → incremental degenerates to strict for that response and the trace says so. Detector slower than generation → backpressure from GW12, not buffer growth.

### Exit criteria

One resolver for both phases. Two modes implemented, selectable, visibly distinct. Separate latency qualification. No truncation-as-block. Boundary safety in both.

### Evidence package

Wire captures for both modes · UNAVAILABLE matrix · MONITOR run · two latency profiles · boundary tests · mode-visibility screenshots.

### Rollback / stop rule

Revert to STRICT_WITHHOLD as the safe default for affected tenants. **Stop** if BLOCK cannot be made truthful in incremental mode — then incremental does not offer BLOCK, and that is documented rather than faked.

### Agent execution notes

Agent-executable. The agent must not implement BLOCK-after-first-byte as truncation; if the mode cannot support it, the mode declares it unsupported.

## GW14 — Build async bounded audit and the honest timing instrument

> **v2.1 CORRECTION (C3, C15, C31).** The DecisionRecord schema is versioned and consumed by GW14b. Stage status must be truthful — the prototype reported five of seven stages as executed unconditionally, and "audit:E" while records were being dropped during a store outage — and every started request gets an audit record, including cancellations. No synchronous I/O on serving loops: a synchronous metrics-file dump stalled a guard owner 200–380 ms every 10 minutes and a per-second histogram dump cost 6.5 ms of loop time per worker. Residual rule: p50 = 0 and |residual| within the declared tolerance at p99.9, otherwise the run is void. The async producer is the fix for the log publisher (switching to INFO removes only one of 2–14 publishes per request). **The SLO must be observable (C39):** a per-request T_fw_addon (maximum over chunks) from socket-level arrival timestamps of the request and of every upstream chunk, loop-lag attribution, a test that blocks the event loop mid-stream (LGW14-1..3 inject delay inside the gateway's own stopwatch and cannot catch loop blocking — at 78 RPS every stage metric read PASS while clients saw 21.9 ms), and a production synthetic client + provider canary. v1's overhead_ms is not inflated by TTFT as §10.11 suggests — it is clamped to 0 for every stream and reads 4–5× low for JSON; the fix (signed residual, no clamp) is unchanged. **Sheds are audited (C40):** every admitted request, including 503 sheds, gets a record and completeness is measured against admitted requests (the prototype's ratio read 1.0 while up to 5.75% of admitted requests were shed without a record); run LGW14-7.


| **Field** | **Value** |
|----|----|
| Phase | Observability |
| Depends on | GW04, GW12 |
| Primary owner | Backend engineer |
| Objective | Exactly one truthful decision record per phase, produced without blocking the request, and a timing instrument that cannot report the provider's latency as ours. |

### Why this task exists

Two defects meet here. The timer starts at main.py:7085 inside the handler, after auth middleware has already run, so stage_metrics\["auth_ms"\] at :7691 measures body parsing rather than authentication. And model_output_ms is derived by subtraction — stream_orchestration.py:683 computes duration_ms − ttft_ms, explicitly excluding TTFT — so provider time-to-first-token lands in overhead_ms and is reported as firewall overhead. Separately, redis_log_handler.py performs a synchronous Redis publish inside logging.Handler.emit(), and main.py:6716-6730 sets the gateway logger to DEBUG, so every log line blocks the event loop.

### Implementation work

Start the request clock in edge/ before identity resolution. Timestamp provider-stream open and close directly; never derive model_output_ms by subtraction.

Record the reconciliation residual wall − Σstages − provider **signed**. Export it as a histogram and fail CI at a non-zero p50. A clamp to zero is prohibited; it is what hid this class of error in v1.

Separate T_input, T_release_lag, T_finalize and T_fw_addon per §1.2, each measured, none derived from the others.

Emit exactly one DecisionRecord per phase, carrying plan version, deciding rules, all findings including SKIPPED and UNAVAILABLE, detector and model hashes, and the transformation verification result.

Audit is a bounded async producer. On backpressure it applies a declared policy — spill or drop — and **counts** what it did; audit_completeness_ratio is exported and loss is never assumed zero.

Replace the synchronous log publisher with the same bounded async producer, and restore the gateway logger to INFO.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW14-1 | Recorder configured with 2,000 ms TTFT and zero firewall work. | T_fw_addon ≈ 0. | Addon tracks TTFT — the v1 defect. |
| LGW14-2 | Inject 5 ms auth, 5 ms input, 7 ms output delays. | Each appears in its own metric, within tolerance. | Any lands in the wrong bucket. |
| LGW14-3 | Inject a 30 ms mid-stream hold. | Appears in release lag; a sub-20 ms claim fails. | Hidden in provider time. |
| LGW14-4 | Force a reconciliation mismatch. | Signed residual recorded; CI fails. | Clamped to zero. |
| LGW14-5 | Stall the audit sink. | Bounded queue; declared policy; loss counted; gateway stable. | Unbounded growth, or silent loss. |
| LGW14-6 | Measure event-loop lag with logging at INFO and DEBUG. | No synchronous publish on the request path in either. | Blocking publish present. |
| LGW14-7 | Join 10,000 records against provider and client bytes. | One record per phase; all joins resolve. | Duplicate, missing or contradictory records. |

### Failure scenarios that must be handled

Sink unavailable at startup → gateway starts, records queue, completeness is reported. Clock adjusted mid-request → monotonic source; injectable for tests.

### Exit criteria

Clock starts pre-identity. Provider time measured. Signed residual with a CI gate. Four §1.2 metrics separately measured. One record per phase. Bounded audit with counted loss. No synchronous logging on the request path.

### Evidence package

TTFT-independence run · injected-delay attribution · mid-stream hold detection · residual histogram · sink-stall run · loop-lag comparison · join report.

### Rollback / stop rule

Revert the instrument revision; measurement stops until it is restored. **Stop** all performance claims if the residual p50 is non-zero — every number produced under a broken instrument is void.

### Agent execution notes

Agent-executable, and a prerequisite for every performance card. No GW20 measurement may be reported before this card exits.

## GW15 — Ship the chat surface on v2 and pass the full conformance suite

> **v2.1 CORRECTION (C2, C21).** Depends on GW01, GW02, GW06, GW09, GW10, GW11, GW13, GW14 (§0.5) and receives the relocated tests LGW04-2, LGW04-4, LGW07-1, LGW07-4, LGW08-1, LGW09-2, LGW11-3, LGW12-7. Add the channel tests: a canary in every SDK request field under BLOCK produces zero provider calls; a canary in every output field under REDACT reaches the client zero times. The prototype passed the equivalent SDK acceptance (Python 2.38.0 sync/async, Node 4.104.0, unmodified-app base_url swap, provider-byte proofs, two tenants × 200 concurrent, guard kill, disconnect) with a closed-port negative control — and failed the channel tests, which is why they are added.


| **Field** | **Value** |
|----|----|
| Phase | Surface |
| Depends on | GW01, GW11, GW13, GW14 |
| Primary owner | Backend + protocol engineer |
| Objective | The first complete request path end to end on v2, satisfying the one frozen contract. |

### Why this task exists

This is where the architecture stops being a diagram. Every layer is exercised by real traffic through the official SDKs, and the base-URL-swap promise becomes testable.

### Implementation work

Implement /v1/chat/completions streaming and non-streaming, /v1/completions and /v1/responses, as thin edge/ adapters over the shared pipeline. Route-specific logic lives in edge/wire/; security logic lives nowhere in edge/.

Wire the full lifecycle of §10.4, including the pre-identity clock and the one shared-state round trip.

Run C1 against v2 in-process and over real TCP, Python and Node.

Run the base-URL-swap acceptance directly: take an unmodified application written against OpenAI, change base_url and the key, and run it.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW15-1 | Full C1 suite, in-process and over TCP, Python and Node. | 100% pass, no xfail. | Any failure or workaround. |
| LGW15-2 | Unmodified OpenAI application, base URL and key swapped only. | Works unchanged, streaming and non-streaming. | Any code change needed — the frozen contract is broken. |
| LGW15-3 | S01–S07 from §5 against v2. | All pass. | Any failure. |
| LGW15-4 | Two-tenant opposite-policy concurrency. | No leakage of config, findings, request IDs or audit. | Any leakage. |
| LGW15-5 | Tool-call streaming with fragmented arguments. | SDK reconstructs the exact call. | Corruption. |
| LGW15-6 | Replay 10,000 C2 chat requests through v1 and v2. | Only IDENTICAL and ledger-registered EXPECTED. | Any UNEXPECTED or WIRE. |

### Failure scenarios that must be handled

An SDK version newer than the pin behaves differently → the pin is the contract; a bump is a deliberate change with a full re-run. A C2 shape v2 has not seen → it is a coverage finding, and it blocks GW21 rather than being waived.

### Exit criteria

C1 100% on both SDKs and both transports. Base-URL swap proven with an unmodified application. §5 S01–S07 pass. Tenant isolation proven. C2 chat replay clean.

### Evidence package

Conformance runs · unmodified-application transcript · scenario results · isolation run · tool-call captures · replay report.

### Rollback / stop rule

v2 serves no production traffic; failures are development state. **Stop** if the base-URL swap requires any client change.

### Agent execution notes

Agent-executable. This is the first card whose completion is externally meaningful; human review before it is marked done.

## GW16 — Ship embeddings, models, moderations and legacy completions

> **v2.1 CORRECTION (C34).** Define and test: a BLOCK in one item of a batch (v1 fails the whole request closed); how per-item decisions map to "one DecisionRecord per phase"; token-array inputs (v1 rejects them as unscannable).


| **Field** | **Value** |
|----|----|
| Phase | Surface |
| Depends on | GW15 |
| Primary owner | Backend engineer |
| Objective | Complete the OpenAI-compatible surface on the same plan and resolver. |

### Why this task exists

/v1/embeddings in v1 runs Tier-1 only, by design, with no semantic option — a gap invisible from the console because the plan and the surface disagree about what is available. In v2 the plan is authoritative for every surface, so a surface either supports a rule or the compiler rejects selecting it.

### Implementation work

Implement /v1/embeddings on the shared pipeline, with the same plan, the same detectors and the same resolver. Batch inputs are scanned per item with per-item findings.

Implement /v1/models and /v1/moderations from the pinned catalogue and the plan.

Implement legacy /v1/completions through the same path.

Where a surface genuinely cannot support a rule, the **compiler** rejects selecting it, with an actionable message, rather than the surface silently ignoring it.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW16-1 | Embeddings batch with PII in item 3 of 10. | Per-item findings; only item 3 transformed; recorder confirms. | Whole batch redacted, or item missed. |
| LGW16-2 | Select a semantic rule scoped to embeddings. | Either it runs, or the compiler rejected it at save. | Accepted and silently ignored. |
| LGW16-3 | C1 embeddings and models tests. | Pass on both SDKs. | Any failure. |
| LGW16-4 | Legacy completions with a firewall block. | Correct error envelope; zero provider calls. | Wrong shape, or a call made. |
| LGW16-5 | C2 replay for these surfaces. | IDENTICAL or ledger-registered EXPECTED only. | Any UNEXPECTED. |

### Failure scenarios that must be handled

Very large batch → bounded by the contract with a clear error, never partial silent processing. Model catalogue empty → the GW11 distinction between provisioning and outage applies here too.

### Exit criteria

All surfaces on the shared pipeline. Per-item embedding findings. Compiler rejects unsupported scopes. C1 green. C2 replay clean.

### Evidence package

Per-item wire captures · compiler rejection transcript · conformance runs · replay report.

### Rollback / stop rule

Surfaces ship independently; an incomplete surface stays on v1 until the cutover. **Stop** if a surface needs its own enablement logic.

### Agent execution notes

Fully agent-executable and parallelizable with GW17 and GW18.

## GW17 — Ship the MCP surface on the same plan and resolver

> **v2.1 CORRECTION (C14, C33).** Tool descriptions ARE scanned in v1; injection in them is not detected by default because default detection is off. The two-flag Tier-2 gate and the separate MCP resolver (3 of 6 comparable cases disagree with chat) are confirmed. Add to scope and tests: resources/read, prompts/get arguments, notifications/progress interleaved in SSE, structuredContent, sampling/createMessage and elicitation, and the stdio line bound; the remote guard channel must be authenticated if it leaves a trusted network.


| **Field** | **Value** |
|----|----|
| Phase | Surface |
| Depends on | GW15 |
| Primary owner | Backend + security engineer |
| Objective | Bring 6,250 lines of independently-evolved MCP proxy onto the shared plan and resolver without losing its genuinely good isolation work. |

### Why this task exists

mcp_proxy.py is 6,250 lines with a single 1,016-line handler and 72 block-decision sites. It has its own Tier-2 enablement requiring **two** independent flags (tier2_ctrl.enabled **and** mcp_tier2_enabled), its own scan orchestrator with a duplicated resolver, and its own floor logic. Its default Tier-1 detection is also effectively off: \_mcp_default_detection_enabled reads a config key that config.py never sets, so it falls through to an environment variable defaulting to OFF.

The isolation work, by contrast, is good and must survive: per-org sandboxes via the broker, SSRF DNS-resolution checks against loopback, link-local and metadata ranges, host allowlists, command allowlists for stdio, and body caps.

### Implementation work

Re-implement the MCP surface as an edge/ adapter: JSON-RPC and REST framing in edge/mcp.py, all security through the shared plan, detectors and resolver. Tool arguments and tool results both become scanned text with findings; the plan decides the action.

Collapse the double gate. One plan, one enablement, identical to chat.

Port sandbox, broker, SSRF and allowlist logic substantially as-is. It is the strongest isolation work in the repository. What changes is that its decisions become findings routed to the resolver rather than independent block sites.

Close the known gap: tool-description prompt injection is explicitly not covered in v1. In v2 tool descriptions are scanned text like any other, and whether that produces an action is the plan's decision.

Keep per-org sandbox isolation and prove it under concurrency.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW17-1 | Identical injection fixture via chat and via MCP tool arguments. | Identical disposition under an identical plan. | Surfaces disagree. |
| LGW17-2 | Enable one semantic rule; make no other change. | MCP scanning active — no second flag required. | Still requires a second enable. |
| LGW17-3 | SSRF attempts at loopback, link-local and cloud metadata. | All blocked; parity with v1 or better. | Any regression in isolation. |
| LGW17-4 | Two orgs, concurrent tool calls to the same upstream. | Separate sandboxes; no credential, result or audit leakage. | Any leakage. |
| LGW17-5 | Tool description carrying an injection payload. | Scanned; finding emitted; plan decides. | Unscanned as in v1. |
| LGW17-6 | Secret split across two tool-result blocks. | Detected; parity with v1's split-secret floor. | Regression. |
| LGW17-7 | C2 MCP replay. | IDENTICAL or ledger-registered EXPECTED only. | Any UNEXPECTED. |

### Failure scenarios that must be handled

Upstream MCP server unreachable → declared error, never a fabricated empty tool list. Sandbox exhausted → bounded queueing and a clear error; never direct dialling as a fallback.

### Exit criteria

MCP on the shared plan and resolver. Single enablement. Isolation at parity or better. Tool descriptions scanned. Concurrency isolation proven. Replay clean.

### Evidence package

Cross-surface parity report · single-flag proof · SSRF matrix · two-org concurrency run · description-scan results · split-secret tests · replay report.

### Rollback / stop rule

MCP stays on v1 until its replay is clean; surfaces cut over together at GW23 but are developed independently. **Stop** if any isolation control regresses.

### Agent execution notes

Agent-executable, largest single porting card. Isolation code is ported, not rewritten; a rewrite here is a security risk with no architectural payoff.

## GW18 — Ship RAG and vector surfaces on the same plan and resolver

> **v2.1 CORRECTION (C6, C13).** llm_judge is not "the last default-on external AI path" system-wide: output Tier-2 (ENABLE_TIER2 default true), the Bedrock credential preflight on every boot and the control-plane security engines are also default-on (their removal is itemized under GW24). LGW18-1 passes with a blatant fixture: measured indirect-injection recall is 1/4 and benign instruction-like documents are flagged 1–3 of 3 — publish measured recall on an indirect-injection family, not a single fixture.


| **Field** | **Value** |
|----|----|
| Phase | Surface |
| Depends on | GW15 |
| Primary owner | Backend + security engineer |
| Objective | Close the retrieved-content gaps and remove the last default-on external AI call. |

### Why this task exists

Three concrete gaps. /v1/vector/query cannot run semantic scanning at all — \_RAG_GUARDRAIL_KEYS at vector_routes.py:210-215 copies four keys and rag_tier2_enabled is never among them — and it also lacks the PII and injection backstops that /v1/rag/query has, so retrieved documents are returned without either. Second, rag_generator_enabled and rag_ranker_enabled both default false, so the stages holding retrieved-content redaction do not run by default. Third, llm_judge.py:166 sends **raw, un-redacted** query text to an external model, is on by default at main.py:6674, and fails open to a regex fallback.

### Implementation work

Bring RAG ingest, RAG query, vector query, vector upsert and retrieved-chunk handling onto the shared plan, detectors and resolver.

Make retrieved content first-class scanned text. A document returned from a vector store is untrusted input and is scanned before it enters a prompt or reaches a client, on every RAG and vector path, not only on the one that has backstops today.

Remove llm_judge and the Titan grounding embedder. Grounding, if selected, runs through GuardBackend against a local model. This is the last default-on external AI path and its removal is a §1 locked position, not an optimization.

Bring the cross-tenant collection guard forward; it is sound. Fix the empty-org_slug path that coerces to "default" at pipeline.py:490-494, which evaluates one tenant against another's compiled policies — the same defect class as §10.1.5, on a different surface.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW18-1 | Poisoned document in the store; retrieve via /v1/rag/query and /v1/vector/query. | Identical handling on both; injection detected on both. | Vector path unprotected, as in v1. |
| LGW18-2 | PII in a retrieved chunk, both paths. | Redacted before reaching prompt or client on both. | Raw PII returned. |
| LGW18-3 | Deny all external AI egress; run every RAG path. | Zero attempted external calls; all paths function. | Any attempt. |
| LGW18-4 | Token with empty org_slug. | Explicit error; never evaluated against another org's policies. | Coerced to default. |
| LGW18-5 | Cross-tenant collection name. | Blocked; parity with v1. | Regression. |
| LGW18-6 | Enable a semantic rule scoped to retrieval. | Active on every retrieval path. | Any path unaffected. |
| LGW18-7 | C2 RAG and vector replay. | IDENTICAL or ledger-registered EXPECTED only. | Any UNEXPECTED. |

### Failure scenarios that must be handled

Vector provider unreachable → declared error, never empty results presented as a clean search. Very large retrieved set → bounded by the contract with a clear error.

### Exit criteria

All RAG and vector paths on the shared plan. Retrieved content scanned everywhere. Zero external AI. Tenant coercion removed. Replay clean.

### Evidence package

Cross-path parity · redaction captures · egress-deny run · empty-org test · cross-tenant tests · replay report.

### Rollback / stop rule

RAG and vector stay on v1 until replay is clean. **Stop** if grounding cannot be delivered locally — then grounding is unsupported and the compiler rejects selecting it, rather than quietly calling a cloud model.

### Agent execution notes

Agent-executable. Removing llm_judge also removes the RAG path's only semantic layer in v1; the local replacement lands **before** the removal, never after.

## GW19 — Implement admission control, overload semantics and graceful drain

> **v2.1 CORRECTION (C7, C23, C30).** Admission bounds every shared resource per tenant, including the guard owner's queue (prototype: owner shedding stopped UNAVAILABLE-by-overload, but a tenant-blind FIFO still let one tenant shed another 30/30). Sheds carry `x-should-retry: false` or an adequate Retry-After (the SDK otherwise triples the load). Worker crashes are isolated and respawned (in the prototype one worker's crash took the whole unit down). Drain is bounded with write/idle timeouts. LGW19-1 moves to GW20.


| **Field** | **Value** |
|----|----|
| Phase | Hardening |
| Depends on | GW06, GW12 |
| Primary owner | Platform engineer |
| Objective | Make behaviour under overload explicit and bounded rather than emergent. |

### Why this task exists

v1 has no admission control. --worker-connections 20000 is silently inert because uvicorn.workers.UvicornWorker does not read it, and rate limits bound arrival rate, not concurrency. The only asyncio.Semaphore in the codebase is in the stdio adapter. Under overload the system therefore has no defined behaviour: queues grow, p99 collapses, memory climbs.

### Implementation work

Concurrency admission derived from the ResourceContract and the measured guard service rate — not a connection count, which is not the scarce resource.

Explicit overload response: a declared status with Retry-After and a request id, emitted **before** latency collapses, with a measured q_safe threshold.

Bounded queues everywhere — request, guard, dispatch, egress, audit — each with an exported depth and age.

Graceful drain: on SIGTERM, stop accepting, finish in-flight streams within a bounded window, flush audit, then exit. Streams exceeding the window get declared termination, not a truncated frame.

Remove the inert --worker-connections and migrate off the deprecated worker class in the same change, so the cap is demonstrably effective rather than cosmetic.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW19-1 | Offer 3× measured q_safe. | Explicit shedding; p99 for admitted requests stays within budget; no OOM. | Unbounded queueing or OOM. |
| LGW19-2 | Ramp arrival rate open-loop to failure. | Highest passing and first failing rate both recorded. | Only the passing point recorded. |
| LGW19-3 | SIGTERM during 500 active streams. | Bounded drain; audit flushed; declared termination past the window. | Truncated frames or lost audit. |
| LGW19-4 | Verify the concurrency cap is effective. | Cap binds and is observable. | Cap is inert, as in v1. |
| LGW19-5 | Guard service rate halved mid-load. | Admission responds; queue age bounded. | Queue grows unbounded. |
| LGW19-6 | Slow consumers on 50% of streams. | Backpressure; bounded memory; fast consumers unaffected. | Fleet-wide degradation. |

### Failure scenarios that must be handled

Shedding threshold too aggressive → tuned from measured service rate, not guessed. Drain window too short → declared and measured, and the number is published.

### Exit criteria

Admission from measured capacity. Explicit overload response. All queues bounded and exported. Drain bounded and proven. Cap demonstrably effective.

### Evidence package

3× overload run · open-loop sweep with both points · drain run · cap-effectiveness proof · degradation response · queue metrics.

### Rollback / stop rule

Revert admission parameters; bounds are versioned with the image. **Stop** if overload produces OOM or unbounded queue growth.

### Agent execution notes

Agent-executable. q_safe is measured in GW20 and fed back here; the agent must not hardcode a provisional value.

## GW20 — Measure one v2 serving unit and prove horizontal scaling

> **v2.1 RESULT (C5, C18, C31, C38, C42).** Measured with the v2 prototype on live GCP (asia-south1; the all-in-one fleet ran in asia-northeast1 after G2 stock-outs), on-demand list prices, HEADLINE 70/30 at ITL 20 ms; pass rule = C4 p99 < 20 ms over all offered requests (errors as +∞), infra ≤ 0.1%, 0 drops. (1) One g2-standard-24 under GW03's derived per-worker guard share: 78 RPS on the end-to-end rule, which is blind to mid-stream delay, with the GPU 11–12% busy — a contract artifact; with random arrivals it sheds 3.5% at 78 RPS. (2) The same unit with the per-worker cap removed, as built: no C4 pass at any rate (a ≈ 20 ms floor from event-loop stalls). (3) With CPU work off the event loops (C38): C4 knee 200 RPS (3 of 3), first fail 250; binding = the input phase (tokenization + a serial batch-1 guard queue) and ≈ 40 ms single-worker stalls. (4) Scaling: as specified 75 / 80 / 80 RPS on 1 / 2 / 4 units; with the cap removed and a shared round-robin edge 150 / 300 / 600 (efficiency 1.00). (5) Off-box guard (C42): a c4-highcpu-16 gateway + a g2-standard-4 guard pass C4 at 191 RPS per L4 = $5.80 per qualified RPS-month. (6) The largest fleet measured within $5,000/month: 3 × c4-highcpu-16 gateways + 4 × g2-standard-4 guards with the loop fix — 520 RPS (511 qualified/s; C4 17.4 / 18.2 / 17.9 ms, 3 of 3) for $4,161.69/month ($4,631.81 with the audit store sized, C41), random arrivals passing at 312 RPS and the 1,024-token edge at 130 RPS; egress excluded (≈ $17.4 per sustained RPS-month). Strict (holdback included) fails at every rate: p99 ≈ 71 ms at ITL 20 (C4). Methodology corrections: 100% per-chunk sampling; "first" = the event completing provider token 1 (98% of first events were a lone space); p99 over all offered requests with errors as +∞; ≥ 5-minute steps with 3 repeats (30-second steps overstated a knee by ~30%); per-task CPU accounting (/proc/stat under-reports bursty CPU on these kernels); open-loop generation with scheduled-time accounting (wrk2's millisecond timers added ~0.6 ms of error); realistic output shapes and non-ASCII input; report per-worker load skew.


| **Field** | **Value** |
|----|----|
| Phase | Performance |
| Depends on | GW15–GW19 |
| Primary owner | Performance engineer |
| Objective | Establish what one v2 unit does, and prove that adding units adds capacity without changing the application. |

### Why this task exists

Every RPS-per-vCPU figure in this project's history is derived, and they span 1,700× — from 0.17 measured today to a struck 520 planning figure. This card replaces all of them with a measurement taken through the GW14 instrument.

### Implementation work

Measure one unit under the signed full-security profile with unique prompts: p50 and p99 of T_fw_addon, T_release_lag, T_finalize; qualified RPS; CPU ms per request from cgroup accounting, not docker stats; RPS per allocated serving vCPU with a complete denominator including guard CPU; RPS per accelerator; guard queue time; q_safe.

Use open-loop arrival-rate load generation. A closed-loop client slows its own offered rate as the server slows and hides overload. Record schedule drops and prove the generator is not the bottleneck.

Sweep CPU allocation across at least four points, holding plan, workload, guard capacity and output mode fixed. Report the highest repeatable passing rate **and** the first repeatable failing rate.

Then scale 1 → 2 → 4 units with the identical image and configuration contract and measure capacity growth and the binding constraint at each step.

Report the three efficiency denominators separately and never interchangeably: RPS per allocated serving vCPU, RPS per purchased node vCPU, and requests per consumed CPU-second.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW20-1 | Single-unit sweep, full profile, unique prompts. | p99 T_fw_addon \< 20 ms at the qualified rate; both boundary points recorded. | Budget missed with no published gap. |
| LGW20-2 | Reconciliation residual across the run. | p50 zero; distribution published. | Non-zero — the run is void. |
| LGW20-3 | 1 → 2 → 4 units. | Material capacity growth; binding constraint named at each step; zero application changes. | Sub-linear with no named constraint, or a code change needed. |
| LGW20-4 | Same image at 2, 4, 8, 16 vCPU. | Four distinct configurations from the contract; capacity tracks. | Any constant value. |
| LGW20-5 | Loadgen headroom check. | Generator sustains the schedule; drops recorded as zero. | Generator is the bottleneck. |
| LGW20-6 | Compare against v1 on identical hardware and workload. | Both measured through the same instrument; difference attributed. | Only v2 measured. |

### Failure scenarios that must be handled

Guard saturates before CPU → named as the binding constraint and reported, not worked around. Scaling is sub-linear → locate the centralized bottleneck before adding hardware; that is the finding.

### Exit criteria

Single-unit numbers through the honest instrument. Three denominators separate. Open-loop with zero drops. 1→2→4 proven with no code change. v1 comparison on identical hardware.

### Evidence package

Sweep with both boundary points · residual distribution · scaling table with binding constraints · four-point contract table · loadgen headroom · side-by-side comparison · raw data for independent recomputation.

### Rollback / stop rule

No rollback — measurement only. **Stop** and void every number if GW14's residual is non-zero at any point in the run.

### Agent execution notes

Agent-executable. The agent must not report a rate that passed once; the criterion is the highest **repeatable** rate, and the adjacent failing point is part of the result.

## GW21 — Run shadow execution to ledger closure

> **v2.1 CORRECTION (C8, C9, C37).** Replace the ≥ 72 h / ≥ 5 M live-shaped window with the synthetic long-run replay of §10.7.3; ledger entries use the ancestry rule; v2 decision records go to a separate stream (GW14b) so dashboards neither double-count nor go blind.


| **Field** | **Value** |
|----|----|
| Phase | Migration |
| Depends on | GW20, GW02 |
| Primary owner | Backend lead + QA |
| Objective | Prove on live-shaped traffic that v2 differs from v1 only where the ledger says it should. |

### Why this task exists

This is the card that makes a single cutover defensible. Replay covers shapes; shadow covers concurrency, tenant mix, plan updates under load and real resource curves — everything a replay cannot reproduce.

### Implementation work

Run v2 in shadow: v1 serves; v2 receives a copy, executes fully against the recorder, records its decision and delivers nothing.

Classify every shadow request into the four buckets. Track UNEXPECTED and WIRE to zero. Each one is either a v2 defect, fixed, or a legitimate behaviour change, which requires a ledger entry authorized **before** the next run.

Run to the declared window: ≥72 hours and ≥5 million requests, covering all four surfaces, both streaming modes, both tenants of the opposite-policy pair, and at least one plan update under load.

Measure v2's resource curve beside v1's on the same traffic, and confirm shadow load does not distort v1's service.

Close C2 coverage gaps found in shadow by extending the corpus, then re-replay.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW21-1 | Full shadow window. | Zero UNEXPECTED, zero WIRE across the whole window. | Any of either. |
| LGW21-2 | Ledger review. | Every EXPECTED entry has a rule id, a §10.2.2 row, a C3 score and a pre-dating commit. | Any post-hoc entry. |
| LGW21-3 | Plan update at 70% load, observed in shadow. | v1 and v2 converge within the same bound. | v2 diverges. |
| LGW21-4 | Surface coverage. | Every surface and both modes exercised above the declared minimum. | Any surface under-covered. |
| LGW21-5 | Shadow overhead on v1. | Within the declared budget; v1 p99 unaffected. | Shadow degrades production. |
| LGW21-6 | Resource comparison. | v2's curve measured and published beside v1's. | Not measured. |

### Failure scenarios that must be handled

A rare shape appears once with an UNEXPECTED diff → investigated to root cause; rarity is not a waiver. Shadow falls behind → it is bounded and drops are counted; a dropped shadow request is not a clean one.

### Exit criteria

Zero UNEXPECTED and zero WIRE across the full window. Ledger complete and pre-dated. Coverage met. Shadow overhead within budget. Resource curves published.

### Evidence package

Shadow report by bucket, surface and day · ledger with timestamps · convergence run · coverage matrix · overhead measurement · resource comparison.

### Rollback / stop rule

Stop shadow; v1 is unaffected throughout. **Stop the cutover** if UNEXPECTED cannot be driven to zero. There is no threshold of acceptable unexplained divergence.

### Agent execution notes

Agent-executable for running and triage. Ledger authorization is human. The agent's output is a triaged diff report, not a decision to accept one.

## GW22 — Rehearse cutover and rollback under load

> **v2.1 CORRECTION (C9, C37).** Rehearse a per-tenant cohort cutover and rollback. Key names do not collide but meanings diverge (C37): a console kill switch or revocation must reach v2, a v2 kill switch must survive rollback to v1, quota needs one ledger, and audit must not share an evicting store with security state — under the repository's production policy (allkeys-lru, 768 MB) v2 audit records evicted every security key of both versions in a scaled test.


| **Field** | **Value** |
|----|----|
| Phase | Migration |
| Depends on | GW21 |
| Primary owner | DevOps + backend lead |
| Objective | Execute the entire cutover and rollback on staging under production-shaped load, and measure the rollback time that will be published. |

### Why this task exists

A single cutover is only survivable if it is a routing change against an already-warm system, and if rollback has been performed rather than assumed. The rehearsal converts both from plan to measurement.

### Implementation work

Bring staging to a production-shaped state: both versions deployed and warm, real edge, shared state, guard fleet, production-shaped load.

Execute the full runbook: pre-checks, freeze, shift traffic, observe, declare. Time every step.

Execute rollback under the same load, starting from a deliberately injected failure rather than a clean state. Measure from decision to full restoration.

Rehearse each documented failure mode: v2 unhealthy after shift; a v2 defect visible only at full traffic; shared-state contention from both versions; in-flight streams at the moment of the shift.

Verify in-flight stream behaviour explicitly: a stream that began on v1 completes on v1 or terminates per the declared contract; it is never spliced onto v2.

Publish the measured rollback time. It becomes the number the cutover decision is made against.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW22-1 | Full cutover under load. | Completes within the declared window; error rate within budget throughout. | Any window or budget exceeded. |
| LGW22-2 | Rollback from an injected failure. | Full restoration within the declared window; measured and published. | Not achievable, or not measured. |
| LGW22-3 | In-flight streams at shift. | Complete on the original version or terminate per contract; zero splices. | Any spliced stream. |
| LGW22-4 | Both versions on shared state under load. | No quota multiplication, no plan corruption, no cross-version leakage. | Any contention effect. |
| LGW22-5 | v2 unhealthy immediately after shift. | Automatic detection; rollback triggered within the declared window. | Manual detection only. |
| LGW22-6 | Full rehearsal, twice. | Same results both times. | Non-reproducible. |

### Failure scenarios that must be handled

Rollback needed after v2 has written state v1 cannot read → state compatibility is verified in this rehearsal, not discovered in production. Edge caches the routing decision → verified and bounded.

### Exit criteria

Cutover and rollback both executed under load, twice, reproducibly. Rollback time measured and published. In-flight semantics proven. No shared-state contention. Automatic unhealthy detection.

### Evidence package

Timed runbook execution ×2 · rollback timing · in-flight captures · contention report · automatic-detection run · signed go/no-go checklist.

### Rollback / stop rule

Staging only; no production exposure. **Stop** if rollback cannot be completed within the declared window under load — the cutover is not authorized without a proven exit.

### Agent execution notes

Rehearsal execution is agent-assisted; the go/no-go decision is human and recorded.

## GW23 — Execute the production cutover

> **v2.1 CORRECTION (C9).** T24 runs before this card as cohort 1 (§0.5). LGW23-1..6 assume production traffic that does not exist today — run them with synthetic probe load and report them as such until tenants exist; move later tenants in cohorts.


| **Field** | **Value** |
|----|----|
| Phase | Migration |
| Depends on | GW22 |
| Primary owner | Backend lead + DevOps + on-call |
| Objective | Move production to v2 in one routing change, with v1 warm and rollback proven. |

### Why this task exists

Everything above exists to make this step boring.

### Implementation work

Re-verify every precondition on the day: shadow still clean, conformance green, GW20 numbers current for the deployed build, rollback rehearsed within the declared freshness window.

Execute the rehearsed runbook. Do not improvise: a deviation is a stop, not an adaptation.

Observe against pre-declared thresholds for error rate, p99 addon, security disposition distribution, provider call ratio and audit completeness. The disposition distribution matters as much as the latency: a sudden shift in block rate is a defect signal even when latency looks excellent.

Hold v1 warm and rollback-ready for the declared period after cutover.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW23-1 | Cutover at low traffic. | Thresholds hold through the observation window. | Any breach → roll back immediately. |
| LGW23-2 | Ramp to full production traffic. | Thresholds hold at every step. | Any breach → roll back. |
| LGW23-3 | Disposition distribution versus shadow. | Within the declared tolerance. | Material shift → investigate before proceeding. |
| LGW23-4 | Real OpenAI SDK clients, Python and Node. | Work unchanged, no client changes. | Any client breaks. |
| LGW23-5 | Audit completeness during and after. | ≥ declared ratio throughout. | Loss above the bound. |
| LGW23-6 | 24-hour post-cutover observation. | All thresholds hold; resource curve matches GW20. | Any drift → roll back. |

### Failure scenarios that must be handled

A defect appears only at production scale → roll back first, diagnose second. A tenant reports a behaviour change → check the ledger; an entry means expected and the tenant is informed, absence means roll back.

### Exit criteria

Cutover complete. Thresholds held through 24 hours. No client changes required. Audit complete. v1 warm through the declared window.

### Evidence package

Timed execution log · threshold dashboards · disposition comparison · SDK verification · audit completeness · 24-hour report.

### Rollback / stop rule

The rehearsed rollback, executed on any threshold breach, without debate. Diagnosis happens after restoration.

### Agent execution notes

Human-led. Agents may monitor and prepare artifacts; the shift and any rollback are executed by a human on-call.

## GW24 — Decommission v1, remove external-AI paths and credentials, publish the evidence pack

> **v2.1 CORRECTION (C13).** Itemize the removal list beyond gateway modules, following T08/T25's scope: control-plane security engines (bedrock_client, bedrock_scanner, integrated_scanner; the apps.py warm-up; `/api/security/scan/` and the `/api/policy/check/` fallback), the shared Gemini client imported by control, the gateway Bedrock credential preflight. Treat the BYOK embedder fallback to platform credentials, the worker's default embedding and keyless bedrock model rows as credential-revocation hazards (LGW24-2, L25-3) and decide them explicitly. LGW24-1's egress denial covers the control plane, workers, start-up and background jobs. The inventory has 17–20 BEDROCK_* names, not 27.


| **Field** | **Value** |
|----|----|
| Phase | Release |
| Depends on | GW23 |
| Primary owner | Backend lead + security owner |
| Objective | Make the old system unreachable, prove the local-only boundary, and leave behind evidence someone else can reproduce. |

### Why this task exists

An un-deleted v1 is a path back to every defect in §10.1. And credential removal must follow code removal: revoking IAM while code still calls Bedrock converts a fail-open path into a per-request AccessDeniedException storm.

### Implementation work

Order is fixed. Remove v1 routes, then v1 code, then external-AI client code, then credentials and network permissions. Never reverse the last two.

Delete the Bedrock, Vertex and Gemini client modules, the 27 BEDROCK\_\* environment variables, llm_judge, bedrock_embedder, bedrock_scanner, bedrock_client, bedrock_logger and bedrock_tier2_breaker. Note that boto3 itself stays: poc_views.py:69 uses boto3.client("s3") and that is unrelated.

Prove the local-only boundary with egress denial across every surface, every failure path, startup, restart, background jobs and retries. The criterion is zero **attempted** platform external-AI calls, not zero successful ones.

Delete the dead code v1 carried: embedding_vault (497 lines, hard-disabled), the always-allow services/guardrails scaffold and its GUARDRAILS_SERVICE_URL wiring, \_scan_prompt_sync_disabled_builtin_default, and the unused opentelemetry dependencies.

Publish the evidence pack: every measurement, its method, its raw data and the commands to reproduce it.

Publish the honest capacity statement: measured qualified RPS at the measured p99, with posture, prompt band, percentile, window count and per-request FPR stated together. A defensible number with its caveats beats an indefensible one without.

### Live acceptance tests — required to exit

| **Test** | **Live procedure** | **PASS** | **FAIL / stop** |
|----|----|----|----|
| LGW24-1 | Deny all external AI egress; exercise every surface, failure path and restart. | Zero attempted calls anywhere. | Any attempt. |
| LGW24-2 | Revoke credentials after code removal; run the full suite. | No behaviour change; no error storm. | Any dependency remains. |
| LGW24-3 | Request a v1 route. | Not found; no v1 process reachable. | Any v1 path alive. |
| LGW24-4 | Independent reviewer reproduces the headline numbers from the pack. | Reproduced within tolerance from the documented commands. | Not reproducible. |
| LGW24-5 | Full conformance and §5 scenario suite on the final build. | All pass. | Any failure. |
| LGW24-6 | Rollback drill to the last v1 image. | Documented as unavailable past the declared date, with the reason. | Implied but untested. |

### Failure scenarios that must be handled

A dependency on a removed module surfaces late → found by egress denial before credential revocation, which is why the order is fixed. Evidence cannot be reproduced → the claim is withdrawn until it can.

### Exit criteria

v1 unreachable and deleted. External AI removed in the correct order and proven by egress denial. Dead code gone. Evidence pack reproducible by a third party. Capacity statement published with all caveats.

### Evidence package

Egress-denial matrix · credential-revocation run · route verification · independent reproduction report · final conformance run · published capacity statement.

### Rollback / stop rule

Past this card there is no rollback to v1; that is the point. **Stop** if egress denial shows any attempt — fix before revoking anything.

### Agent execution notes

Agent-executable except credential revocation. The agent must not remove a credential before its code path is gone, even when the code appears unused.

## 10.10 Backend rebuild live scenario catalog

These extend §5 rather than replacing it. §5's S01–S40 apply to v2 unchanged and are re-run on the candidate build. The scenarios below exist only because a rebuild and a cutover create failure modes a single-system runbook has no reason to describe.

| **ID** | **Scenario** | **Live setup** | **Pass condition** |
|----|----|----|----|
| SB01 | Finding survival | Semantic BLOCK with no matching deterministic rule. | Reaches the resolver; zero provider calls. Directly falsifies the v1 adapter defect. |
| SB02 | Degraded independence | Semantic detector UNAVAILABLE across all four dispositions. | Emitted action derives from the rule's posture, never a blanket redact. Falsifies P3/P6. |
| SB03 | Monitor inertness | MONITOR rule, high-confidence finding, both phases. | Disposition unchanged; finding recorded. Falsifies P7. |
| SB04 | Org precedence | Org ALLOW against a detector BLOCK recommendation. | Signed rule semantics, matching the ledger entry. Falsifies P1/P2. |
| SB05 | Developer traffic | The five backtick prompts plus the tool-description family. | Zero terminal blocks; findings as signal. Falsifies P8. |
| SB06 | Plan-state trichotomy | Empty plan, unavailable plan, unknown tenant. | Three distinct outcomes; no inheritance in any. |
| SB07 | Cross-surface identity | One injection fixture via chat, MCP args, RAG retrieval, vector query, embeddings. | Identical disposition under an identical plan on all five. |
| SB08 | Capacity derivation | Same image at 2/4/8/16 vCPU and two memory limits. | Every bound changes; none constant. |
| SB09 | Literal gate | Introduce a capacity literal outside runtime/. | CI fails naming file and line. |
| SB10 | Layer gate | Introduce an upward import and a detector-side 403. | Both gates fail. |
| SB11 | Resolver purity | Property suite, 10,000 generated cases. | All four properties hold; function deterministic. |
| SB12 | Base-URL swap | Unmodified OpenAI application, key and base URL changed only. | Works, streaming and non-streaming, Python and Node. |
| SB13 | Shadow closure | Full shadow window, all surfaces. | Zero UNEXPECTED, zero WIRE. |
| SB14 | Ledger integrity | Attempt a post-hoc ledger entry. | Rejected on timestamp. |
| SB15 | Cutover rehearsal | Full cut plus rollback under load, twice. | Both within declared windows, reproducible. |
| SB16 | In-flight at shift | Active streams at the routing change. | Complete on origin or terminate per contract; zero splices. |
| SB17 | Dual-version state | Both versions on shared state under load. | No quota multiplication, no plan corruption, no leakage. |
| SB18 | Instrument honesty | Recorder at 2,000 ms TTFT with zero firewall work. | T_fw_addon ≈ 0; residual p50 zero. |
| SB19 | Mid-stream hold | 30 ms hold injected mid-stream. | Appears in release lag; a sub-20 ms claim fails. |
| SB20 | FPR publication | Benign corpus at 1/2/4/7 windows. | Per-request FPR published at every deployed window count. |
| SB21 | Removal order | Revoke credentials before code removal, in staging. | Failure is detected and the order is enforced by the runbook. |
| SB22 | Reproduction | Third party rebuilds the headline numbers from the pack. | Reproduced within tolerance. |

## 10.11 Cutover gate and stop conditions

> **v2.1 ADDITIONS.** Gate rows: **Console contract** — GW14b L14b-1..6 green; **Edge** — GW16b green; **Channels** — zero canary bytes through every SDK request and response field (C21); **Resolver** — per-finding properties hold (C22); **Semantic quality** — recall and request-level FPR published per window count, language and family at the deployed thresholds, with the T01-signed floors met (C6); **Tenant fairness** — tenant B's share and p99 hold while tenant A saturates (C23); **Guard serving** — one owner per GPU, zero FAIL_OPEN-by-overload events in GW20 runs (C7).


§6's release gate applies in full to the v2 candidate. The rows below are additional and specific to replacing a running system.

| **Gate** | **PASS requirement** |
|----|----|
| Wire contract | C1 100% on Python and Node, in-process and over real TCP, on the candidate build. An unmodified OpenAI application works with only base_url and key changed. |
| Parity | Zero UNEXPECTED and zero WIRE diffs across the full shadow window. |
| Ledger | Every EXPECTED diff pre-registered with a rule id, a §10.2.2 row, a C3 score and a pre-dating commit. |
| Detection | C3 recall and FPR published per posture and family, including paraphrase and developer traffic. Every §10.2.2 change scored. |
| FPR | Per-request false-positive rate published at every deployed window count, not per window. |
| Structure | All five structural gates green: layers, function and module length, no decision outside resolve/, no capacity literal, no module-level mutable state. |
| Instrument | Reconciliation residual p50 zero. Every performance number produced under it. |
| Rollback | Rehearsed twice under load; measured restoration time published and current. |
| Local only | Zero attempted platform external-AI calls across all surfaces and failure paths. |
| Credentials | The GW00 exposure resolved and the rotation record signed. |

### 10.11.1 Stop conditions specific to the rebuild

In addition to §6.1, the cutover stops if any of the following is true:

Any UNEXPECTED diff remains unexplained. There is no acceptable threshold of unexplained divergence.

Any ledger entry post-dates the run it excuses.

Any structural gate is disabled, waived or has an exemption list.

Any capacity literal is merged with a comment promising later derivation.

Any surface retains its own enablement logic, plan lookup or resolver.

Any performance number is quoted from a run whose reconciliation residual was non-zero.

Rollback has not been executed under load within the declared freshness window.

The published capacity claim omits posture, prompt band, percentile, window count or per-request FPR.

## 10.12 Reconciliation with the T00–T26 track

> **v2.1 CORRECTION (C16).** "Nothing in §4 is discarded", but §10.13 lets an agent exit on its own card's tests, so T acceptance tests missing from the absorbing card would be skipped: L05-4 missed-notification reconcile, L07-2 encodings/confusables, L07-3 Tier-1 CPU, T03's "all started requests" accounting, L09-1 cold-start readiness, L09-3 windows/s curve, L11-1 incremental timing, L13-3 tenant-specific fallback, L13-4 idempotency, L14-2/3 kill-switch flip and dropped pub/sub, L15-3 dashboard contention, all of T16, T17's per-tenant queues and L17-4, L19-4, L25-3/4, L26-2. Each is added to its absorbing card. T16 is owned by GW16b. T24 runs before GW23 (C9).


Nothing in §4 is discarded. Each T-task is absorbed, retained or superseded, and this table is the authority when the two sections appear to disagree.

| **T-task** | **Disposition under the rebuild** |
|----|----|
| T00 identity and security unknowns | **Retained, strengthened** as GW00. Adds three-branch resolution and the structural gates. |
| T01 sign the contracts | **Retained.** §1–§2 stand. §10.2 narrows the frozen contract to the wire and explicitly unfreezes the internals. |
| T02 Docker staging and recorder | **Retained** and reused by GW02, which cherry-picks staging/t02/ rather than rebuilding it. |
| T03 repair timing | **Superseded by GW14.** v2 measures rather than repairs; no derived model_output_ms exists to fix. |
| T04 capability corpus | **Retained** as C3 and made load-bearing: it scores every deliberate behaviour change. |
| T05 compile the org plan | **Absorbed into GW05**, extended with three distinct plan states and one lookup for all surfaces. |
| T06 one enforcement resolver | **Absorbed into GW07**, extended with purity, type-level BLOCK unreachability and a property suite. |
| T07 fast deterministic Tier-1 | **Absorbed into GW09**, extended with single-redactor byte verification and the injection-to-signal demotion. |
| T08 zero external AI boundary | **Retained**, executed across GW18 (removal) and GW24 (proof and credential order). |
| T09 local input inference | **Absorbed into GW08 and GW10**, extended with the pluggable backend and the per-request FPR budget. |
| T10 output security and modes | **Absorbed into GW13**, extended by using the same resolver for both phases. |
| T11 SSE correctness | **Absorbed into GW12**, extended with credit-based backpressure and real cancellation. |
| T12 SDK compatibility | **Elevated to GW01** and promoted from a task to the frozen contract that gates every commit. |
| T13 routing, retry, cancellation | **Absorbed into GW11 and GW12.** |
| T14 shared-state latency | **Absorbed into GW06**, extended with one posture table implemented once. |
| T15 telemetry off the hot path | **Absorbed into GW14**, extended with counted loss and removal of the synchronous log publisher. |
| T16 edge hardening | **Retained** as deployment work, plus the nginx upstream/keepalive gap from §10.1.6. |
| T17 admission and backpressure | **Absorbed into GW19.** |
| T18 measure one unit | **Absorbed into GW20**, extended with the three separate denominators and open-loop generation. |
| T19 horizontal scaling | **Absorbed into GW20.** |
| T20 qualify the budget envelope | **Retained** and re-run on the v2 candidate. |
| T21 resilience and chaos | **Retained**, extended by GW22's dual-version and in-flight scenarios. |
| T22 frontend control under load | **Retained**, and now runs against v2 as part of UI12. |
| T23 final qualification | **Retained.** Runs on the v2 candidate before GW23. |
| T24 real-provider canary | **Retained.** Runs after GW23 on v2. |
| T25 remove legacy paths | **Absorbed into GW24** with the fixed removal order. |
| T26 publish evidence | **Absorbed into GW24.** |

Sequencing: T00→T02 and T04 run first and serve both tracks. The GW track then replaces T03 and T05–T19. T20–T24 run on the v2 candidate. UI00–UI13 proceed in parallel throughout and must reach UI12 against v2, not v1 — the frontend's backend contract is set by GW05 and GW14, so UI06 and UI07 depend on those two cards rather than on T-track equivalents.

## 10.13 Agent execution protocol

> **v2.1 CORRECTION (C19).** Human authority is required at more than four points: GW00 credential disposition; GW02/GW21 ledger entries; GW06 posture table; GW07 merge review; GW15 review before done; GW22/GW23 go/no-go and rollback; GW24 credential revocation and the independent reviewer; T01 contract signature. "Exits on a command that returns a status" does not replace these, and a command only counts if a negative control proves it can fail.


This track is executed by coding agents with human gate review. The cards are written accordingly, and four rules govern how they are worked.

**A card is one unit of work.** An agent takes one card, works only within its declared file scope, and stops at its exit criteria. Cards do not spill: a defect discovered in another card's scope is reported, not fixed in passing.

**Machine-checkable exit criteria only.** Every card exits on a command that returns a status, not on a judgement. That is why §10.2.3's structural rules are lints and AST gates rather than review conventions — an agent cannot be trusted to remember a convention across a 25-card track, and a human reviewer cannot be expected to catch the 283rd decision site by eye.

**The gates are not negotiable by the agent.** An agent that cannot satisfy a gate reports the obstruction. It does not add an exemption, widen an allowlist, weaken an assertion, mark a test xfail, or introduce a literal with a comment promising later derivation. Every one of those has a specific prohibition in a card above because every one of them is how the current codebase reached its present state.

**Human authority is reserved at four points**, and only four: the credential decision in GW00; every ledger entry in GW02 and GW21; the merge review for GW07, which is the one card where a subtle error is a security failure rather than a bug; and the go/no-go and any rollback in GW22 and GW23.

Recommended lane assignment for parallel agents after GW00: one lane takes GW01 → GW02 (contract and harness); one takes GW03 → GW04 → GW06 (runtime and admission); one takes GW05 → GW07 (plan and resolver, the critical path); one takes GW08 → GW09/GW10 (detection). The lanes converge at GW15, after which GW16, GW17 and GW18 fan out again and rejoin at GW19.
