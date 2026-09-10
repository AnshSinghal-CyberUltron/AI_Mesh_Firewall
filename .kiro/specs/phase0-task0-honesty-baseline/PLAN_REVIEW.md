# P0.0 plan review — Devil's Advocate lock

**Date:** 2026-09-10  
**Worktree:** `/home/contact_cyberultron_com/aimesh-p0-task0`  
**Branch / SHA:** `feat/p0-task0-honesty-baseline` @ `a67337fb`  
**Reviewed artefact:** `brainstorm.md` (pre-shrink)  
**Product code:** unchanged. This document only locks measurement scope.

## Reviewers

| Lens | Agent | Status |
|---|---|---|
| False positives (assume current code is correct) | [P0.0 false positives](57b399ee-80c7-4a14-ba97-7946c910a58c) | completed |
| Hidden failures (assume implementation is broken) | [P0.0 hidden failures](2a8d925e-441e-435a-8616-9a179bd86db0) | **aborted by user — not resumed** |
| State / cache / sync | [P0.0 stale state](773b147b-2a78-4e46-afe3-c72f0fd3fcb0) | completed |
| Observability mismatch | [P0.0 observability](14f0cbe5-7787-4728-b956-f1bb3c6fb6e7) | completed |
| Edge cases | [P0.0 edge cases](d1786760-09a9-46a9-9182-861ec3d28dc7) | completed |

Hidden-failure coverage is taken from the other four reviews plus the Devil's Advocate below. A stopped review is not a green review.

## Answers to brainstorm §10

1. **Is P0.0 the wrong first task?** Partially. On this SHA there is **no** `2026-08-27-task-tracker.md`. Gate 0.1 (corpus) is already committed. Gate 1.1 (stream anchor) is a later spec and is **not** on this SHA. P0.0 may still run as an **honesty baseline of the live non-stream tax**, but it must not certify a 20 ms SLO, must not claim to be tracker "Task 0", and must not treat streaming as measurable tax.
2. **Is a stub LLM enough to call the path complete 9-stage?** Only for **tax isolation**, and only when stub duration > 0 so `output_guardrail` actually runs, input_scan p50 > 0, and `capacity_eligible` is **false**. A stub wall under 20 ms is not a product SLO and not capacity.
3. **Do remapped ports count as Docker images up?** Yes, if images are built from this worktree and `COMPOSE_PROJECT_NAME=aimf_p0`. Publishing nine host ports and bringing frontend/CORS/mcp-stub is **not** required. Unpublishing colliding 5432/6379 (the `aimesh-dev` overlay pattern) is sufficient. Attaching to `aimeshperf` or `ai_mesh_firewall` does **not** count.
4. **Can we talk &lt;20 ms before G1.1?** Only as a **labelled non-stream firewall tax** on the allow-path cell, never as UI Duration, `ttft_ms`, streaming total, docker CPU%, or stub wall. Streaming vs 20 ms is instrument-invalid until G1.1.
5. **Untestable → N/A, not FAIL:** in-process L4 PG2 4.1/5.9 ms; 1,064 RPS / $4,561 (Sep 2 matrix, off-SHA); 100k RPS; Kafka/ClickHouse; live provider cell if no key; T2-on cell if Bedrock TCP-fails (record the error).

## What the brainstorm got wrong (accepted)

| Finding | Source | Lock |
|---|---|---|
| Honesty harness, addon split, nine stage names, stub capacity fail, T-S1, G0.1 corpus are **already on this SHA**. Re-proving them is not P0.0. | false positives | Re-run existing unit honesty tests; do not rewrite `compute_full_nine_stages`. Do not re-lint G0.1. Do not live-reprobe paraphrase (that is G0.2). |
| Scoring *every* `docs/plans/` claim duplicates older `[M]/[R]/[D]` tables and pulls in **off-SHA** Aug 27 / Sep 7 docs. | false positives | Score a **closed claim list** (user 20 ms tax/wall, MASTER ≤12 ms CPU-only, stub ≠ capacity, PG2 N/A, T2-on seconds if measured). |
| Streaming cell B as a 20 ms check is G1.1. On this SHA, stream `total_latency_ms` is provider-stream time. | observability | Cell B is optional smoke; **forbidden** as a 20 ms comparator. |
| `pipeline_9stage_verify.py` is a routing script (real LLM, wrong port, no tax split). | edge cases | **Forbidden** as the P0.0 driver. Reuse `gateway_pipeline_bench.py`. |
| Compose `ENABLE_TIER2` defaults **true**; stub duration defaults **0**; `POLICY_SIGNING_KEY` in `env_file` is ignored. | stale state + edge cases | Overlay `environment:` must pin all three. |
| Allow-path A–D only measures clean short prompts. Block/redact/scans-off/empty-policy/429/401 mixed into A p50 fakes 20 ms. | edge cases | Separate labelled cells; never average with A. |
| Unpinned `WEB_CONCURRENCY` / no `cpus:` makes "max CPU" a host-core artefact. | edge cases | Pin both; record cgroup. |
| `full_nine_stages` only checks `input_scan` and `output_guardrail` p50 > 0, not all nine names. | observability | Do not treat the flag as "all nine ran". Record per-stage p50 and skip/`action`. |
| Redis `firewall:config`, last-good bundles, reused volumes, and image≠SHA make latency look like this SHA while measuring another tree. | stale state | Fresh volumes, digest gate, `/health` policy_count matches seed. |

## What the brainstorm got right (kept)

- Isolated worktree. Do not edit dirty `ansh`. Do not attach `aimeshperf`.
- No production-code changes. Overlay + harness + evidence only.
- Tax ≠ wall. Stub ≠ capacity.
- Policy and input_scan stay serial (HLD). P0.0 does not parallelise them.
- Signing-key / `policy_count: 0` silent no-detection is a harness fail, not a 20 ms win.

## Devil's Advocate

**Challenge: "Just publish cell A tax and stop."**  
A short unique "Reply OK" on an empty policy cache, T2 cache hit, or 1-token stub will print a 20 ms-looking tax while `full_nine_stages` is a two-timer heuristic. Without preflight (`policy_count`, stub duration > 0, image digest, T2 TTL 0, scans on) the pack is a lie. **Rejected as the whole task; accepted as the primary SLO-comparison cell after gates.**

**Challenge: "Bring up the full product stack including frontend."**  
Chat tax is `POST /v1/chat/completions` on the gateway. Vite proxy and CORS are a different product. **Rejected.** Frontend down is N/A unless someone measures via the proxy.

**Challenge: "Copy `aimesh-dev` compose.perf.yml and its optimised gateway."**  
That branch changed product code and already reports 13.6 RPS at p99 17.60 ms on a **different SHA**. Using it would measure the optimised tree, not `a67337fb`. **Rejected.** Isolation pattern (unpublish ports, `environment:` pins, stub duration > 0, T2 TTL 0, pinned workers/cpus) may be **re-implemented** as `compose.p0.yml` in this worktree.

**Challenge: "The user asked for &lt;20 ms and max CPU on the complete 9-stage pipeline, so FAIL the task if A tax > 20 ms."**  
MASTER on this SHA already targets ≤12 ms tax and records today in the seconds class when T2 is on. A FAIL vs 20 ms tax is an honest scorecard row, not a licence to change scanner/PG2/code in P0.0. **P0.0 success is the pack, not the SLO.**

**Challenge: hidden failures the aborted review would have hunted.**  
Likely: unsigned bundle + `/health` 200; Redis override after boot; gunicorn workers with mixed `policy_count`; `up` without `--build`; host `.env` interpolating `ENABLE_TIER2=true`; stub duration 0 → six-stage path; attaching named volumes from `aimeshperf`; quoting `honesty.full_nine_stages` as nine timers. Requirements must fail-close on those. Residual risk: Agent 2 did not independently verify, so preflight is **mandatory** rather than best-effort.

## Locked P0.0 scope (input to requirements.md)

**In:** isolated `aimf_p0` Docker overlay on SHA `a67337fb`; preflight; reuse existing bench; primary **non-stream allow-path tax** (cell A); labelled separate cells for block / redact / scans-off-or-empty-policy / ~10k chars / pinned-CPU; 429 and 401 smokes excluded from p50; evidence JSON+md; small claim scorecard; `capacity_eligible=false` on stub.

**Out:** product code; PG2; G0.2 scoring; G1.1 fix; UI Duration parity; nine-port remap; frontend; mcp-broker; attaching `aimeshperf`; averaging unlike cells; quoting stub RPS as capacity; certifying 20 ms.

**Next:** `requirements.md` in this folder, then a requirements review, then design/tasks. No Docker bring-up until tasks are reviewed.
